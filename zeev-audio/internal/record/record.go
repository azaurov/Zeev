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
)

// Record records up to maxSeconds of audio from dev, optionally applying
// voice-activity detection (VAD) to cut the recording when speech stops.
// rate is the capture sample rate in Hz (0 → 16000). Returns raw WAV bytes.
func Record(dev string, maxSeconds float64, vad bool, rate int) ([]byte, error) {
	if dev == "" {
		dev = "plughw:wm8960soundcard,0"
	}
	if maxSeconds <= 0 {
		maxSeconds = 8
	}
	if rate <= 0 {
		rate = defaultRate
	}

	// Calculate max samples.
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

	deadline := time.Now().Add(time.Duration(maxSeconds*1.5) * time.Second)
	speechEnd := time.Time{}
	silenceThreshold := 400 // RMS energy threshold for VAD

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
				if rms < float64(silenceThreshold) && !speechEnd.IsZero() {
					// 0.5s of silence after speech → stop.
					if time.Since(speechEnd) > 500*time.Millisecond {
						log.Printf("record: VAD silence detected, stopping")
						break
					}
				} else if rms >= float64(silenceThreshold) {
					speechEnd = time.Now()
				}
			}
		}
		if err != nil {
			break
		}
	}

	cmd.Process.Kill()
	cmd.Wait()

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
