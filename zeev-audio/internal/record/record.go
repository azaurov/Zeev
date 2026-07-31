// Package record wraps arecord + VAD to capture voice input and return WAV bytes.
package record

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"log"
	"math"
	"os/exec"
	"time"
)

const (
	defaultRate = 16000
	channels    = 1
	bitDepth    = 16

	// defaultSilenceRMS is only a fallback for callers that cannot supply a
	// measured floor. It is deliberately low, which means "never cuts early"
	// rather than "cuts too early" — failing toward a full-length recording
	// loses latency, while failing the other way truncates the user mid-word.
	defaultSilenceRMS = 400

	// silenceAfterSpeech is how much quiet ends a recording once the speaker
	// has actually said something. Long enough to survive the pause between
	// sentences and the cadence of Hebrew recitation / scripture reading.
	silenceAfterSpeech = 1500 * time.Millisecond

	// noSpeechLimit gives up when NOTHING is ever said. Without it, raising
	// maxSeconds to give real utterances room would make every false wake sit
	// there recording the empty room for the entire ceiling.
	noSpeechLimit = 4 * time.Second
)

// Record records from dev until the speaker stops talking, or until maxSeconds
// elapses — whichever comes first. maxSeconds is a CEILING, not a target: with
// VAD working, an ordinary short question returns in a couple of seconds.
//
// silenceRMS is the int16 RMS below which a frame counts as silence; 0 selects
// defaultSilenceRMS. Pass the real room floor whenever it is known. The old
// hardcoded 400 was measured to sit 3-5x BELOW this room's actual noise floor
// (~1300-1900 RMS), so every frame read as speech, speechEnd was refreshed
// continuously, and the silence rule could never fire — six hours of logs
// showed zero VAD stops. That made maxSeconds the only thing that ever ended a
// recording, so every capture ran the full duration and any utterance longer
// than it was cut mid-word.
//
// rate is the capture sample rate in Hz (0 → 16000). Returns raw WAV bytes.
func Record(dev string, maxSeconds float64, vad bool, rate int, silenceRMS float64) ([]byte, error) {
	if dev == "" {
		dev = "plughw:wm8960soundcard,0"
	}
	if maxSeconds <= 0 {
		maxSeconds = 8
	}
	if rate <= 0 {
		rate = defaultRate
	}
	if silenceRMS <= 0 {
		silenceRMS = defaultSilenceRMS
	}

	// THREE independent limits bound this recording, and they must move
	// together or the smallest silently wins: arecord's own -d (EOF on the
	// pipe), bufSize (the `for n < bufSize` bound), and deadline below.
	// Enlarging one alone reproduces the truncation bug with more allocation.
	maxSamples := int(maxSeconds * float64(rate))
	bytesPerSample := bitDepth / 8 * channels
	bufSize := maxSamples * bytesPerSample

	cmd := exec.Command("arecord",
		"-D", dev,
		"-f", "S16_LE",
		"-r", fmt.Sprintf("%d", rate),
		"-c", fmt.Sprintf("%d", channels),
		"-d", fmt.Sprintf("%.0f", math.Ceil(maxSeconds)),
	)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("arecord stdout: %w", err)
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("arecord start: %w", err)
	}

	buf := make([]byte, bufSize)
	n := 0
	tmp := make([]byte, 4096)

	// Log the threshold BEFORE recording. Whether it is calibrated against the
	// speaker's actual level is the one thing that decides between "cuts on
	// time", "never cuts" and "cuts while they are still talking", and without
	// this line a capture that simply runs to the ceiling logs nothing at all.
	if vad {
		log.Printf("record: start max=%.0fs silence_rms=%.0f", maxSeconds, silenceRMS)
	}

	started := time.Now()
	peakRMS := 0.0
	deadline := started.Add(time.Duration(maxSeconds*1.5) * time.Second)
	speechEnd := time.Time{}
	speechSeen := false
	relaxed := false

	// 0.1s of audio in bytes (for VAD chunk size).
	vadChunkBytes := int(float64(rate) * 0.1 * 2)

	for n < bufSize {
		if time.Now().After(deadline) {
			break
		}
		nr, err := stdout.Read(tmp)
		if nr > 0 {
			written := copy(buf[n:], tmp[:nr])
			n += written

			if vad {
				// Check RMS of last ~0.1s chunk.
				chunkEnd := n
				chunkStart := chunkEnd - vadChunkBytes
				if chunkStart < 0 {
					chunkStart = 0
				}
				rms := rmsInt16(buf[chunkStart:chunkEnd])
				if rms > peakRMS {
					peakRMS = rms
				}
				if rms >= silenceRMS {
					speechEnd = time.Now()
					speechSeen = true
				} else if speechSeen {
					if time.Since(speechEnd) > silenceAfterSpeech {
						log.Printf("record: VAD silence after %.1fs speech, stopping (rms %.0f < %.0f)",
							time.Since(started).Seconds(), rms, silenceRMS)
						break
					}
				} else if time.Since(started) > noSpeechLimit {
					// Nothing has cleared the threshold yet. Two very different
					// causes, and guessing wrong truncates the user mid-word:
					// either the room really is empty, or the threshold was
					// calibrated above this speaker's level. peakRMS separates
					// them. If there is clearly audio here, distrust the
					// threshold rather than the speaker -- relax once to half
					// the observed peak and keep recording. A genuinely silent
					// room has no peak to relax to and still stops on time.
					if !relaxed && peakRMS >= defaultSilenceRMS*2 {
						relaxed = true
						was := silenceRMS
						silenceRMS = math.Max(defaultSilenceRMS, peakRMS*0.5)
						speechEnd = time.Now()
						speechSeen = true
						log.Printf("record: no speech at threshold %.0f but peak was %.0f "+
							"— relaxing to %.0f and continuing", was, peakRMS, silenceRMS)
					} else {
						log.Printf("record: no speech within %s, stopping (peak %.0f < %.0f)",
							noSpeechLimit, peakRMS, silenceRMS)
						break
					}
				}
			}
		}
		if err != nil {
			break
		}
	}

	cmd.Process.Kill()
	cmd.Wait()

	// peak vs silence_rms is the whole diagnosis. peak below the threshold means
	// the threshold is calibrated above this speaker and the capture was cut
	// short; a full-length capture with peak far above it means the opposite --
	// the threshold is under the room floor and VAD never fired.
	if vad {
		secs := float64(n) / float64(rate*bytesPerSample)
		log.Printf("record: done %.1fs of %.0fs max, peak_rms=%.0f silence_rms=%.0f speech=%v",
			secs, maxSeconds, peakRMS, silenceRMS, speechSeen)
	}

	pcm := buf[:n]
	return makeWAV(pcm, rate), nil
}

func rmsInt16(data []byte) float64 {
	if len(data) < 2 {
		return 0
	}
	var sum float64
	count := 0
	for i := 0; i+1 < len(data); i += 2 {
		sample := int16(binary.LittleEndian.Uint16(data[i : i+2]))
		sum += float64(sample) * float64(sample)
		count++
	}
	if count == 0 {
		return 0
	}
	return math.Sqrt(sum / float64(count))
}

func makeWAV(pcm []byte, sampleRate int) []byte {
	dataSize := len(pcm)
	fileSize := 36 + dataSize
	byteRate := sampleRate * channels * bitDepth / 8
	blockAlign := channels * bitDepth / 8

	var buf bytes.Buffer
	buf.WriteString("RIFF")
	binary.Write(&buf, binary.LittleEndian, uint32(fileSize))
	buf.WriteString("WAVEfmt ")
	binary.Write(&buf, binary.LittleEndian, uint32(16))        // chunk size
	binary.Write(&buf, binary.LittleEndian, uint16(1))         // PCM
	binary.Write(&buf, binary.LittleEndian, uint16(channels))
	binary.Write(&buf, binary.LittleEndian, uint32(sampleRate))
	binary.Write(&buf, binary.LittleEndian, uint32(byteRate))
	binary.Write(&buf, binary.LittleEndian, uint16(blockAlign))
	binary.Write(&buf, binary.LittleEndian, uint16(bitDepth))
	buf.WriteString("data")
	binary.Write(&buf, binary.LittleEndian, uint32(dataSize))
	buf.Write(pcm)
	return buf.Bytes()
}
