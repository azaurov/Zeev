package server

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os/exec"
	"strings"
	"time"

	"github.com/azaurov/zeev-audio/internal/audio"
	"github.com/azaurov/zeev-audio/internal/bt"
	"github.com/azaurov/zeev-audio/internal/health"
	"github.com/azaurov/zeev-audio/internal/music"
	"github.com/azaurov/zeev-audio/internal/piper"
	"github.com/azaurov/zeev-audio/internal/proto"
	"github.com/azaurov/zeev-audio/internal/record"
)

// State shared across handlers, set once at startup.
type State struct {
	PiperProc         *piper.Proc
	PiperBin          string
	PiperModel        string
	RemotePiperURL    string       // e.g. https://ollama.sogdiana-gematria.net/piper/tts (bosgame)
	RemotePiperKey    string       // X-Zeev-Key value for RemotePiperURL
	RemotePiperVoice  string       // Kokoro voice name, e.g. "af_heart"; empty = server default
	RemotePiperClient *http.Client // shared, keep-alive-tuned client — see remotePiperSynth

	// Second, independent Kokoro backend (e.g. feiergente01 over LAN). When
	// set, speakPiper alternates chunks between the two machines so they
	// synthesize concurrently without the same-CPU contention that made
	// multiple concurrent requests to a single backend slower (measured).
	RemotePiperURL2 string
	RemotePiperKey2 string
}

// newRemotePiperClient builds an http.Client whose Transport keeps the
// connection to bosgame alive across the multi-minute gaps typical between
// conversational turns. A fresh http.Client per request (the old behavior)
// still shares Go's default connection pool, but its default IdleConnTimeout
// (90s) is shorter than a normal think-then-speak gap, so most replies paid a
// full TCP+TLS handshake before Kokoro could even start inferring.
func newRemotePiperClient() *http.Client {
	return &http.Client{
		Timeout: 45 * time.Second,
		Transport: &http.Transport{
			MaxIdleConns:        4,
			MaxIdleConnsPerHost: 4,
			IdleConnTimeout:     10 * time.Minute,
		},
	}
}

// handle dispatches a single request and returns the response.
func (s *Server) handle(req proto.Request) proto.Response {
	base := proto.Response{ID: req.ID}

	switch req.Cmd {
	// ── audio_dev ──────────────────────────────────────────────────────────
	case "audio_dev":
		base.OK = true
		base.Dev = bt.AudioDev()

	// ── speak / speak_sync ─────────────────────────────────────────────────
	case "speak", "speak_sync":
		dev := req.Dev
		if dev == "" {
			dev = bt.AudioDev()
		}
		var err error
		// Remote backend (bosgame) also handles Russian/Spanish via dedicated
		// Piper models — see speakPiper. Local PiperProc and the second
		// backend (feiergente01) are English-only.
		remoteExtraLangs := req.Lang == "ru" || req.Lang == "es"
		canRemote := s.state.RemotePiperURL != "" && (req.Lang == "" || req.Lang == "en" || remoteExtraLangs)
		canLocal := s.state.PiperProc != nil && (req.Lang == "" || req.Lang == "en")
		if canRemote || canLocal {
			err = s.speakPiper(req.Text, dev, req.Cmd == "speak_sync", req.Voice, req.Lang)
		} else {
			err = fmt.Errorf("piper not available; use espeak-ng fallback")
		}
		if errors.Is(err, audio.ErrSpeechCancelled) {
			// Interrupted on purpose (device-mode button). Not a failure --
			// falling back here would re-speak the entire reply.
			log.Printf("speak: cancelled")
			err = nil
		} else if err != nil {
			// Fallback: espeak-ng → aplay
			log.Printf("speak: piper failed (%v), falling back to espeak-ng", err)
			err = speakEspeak(req.Text, dev)
		}
		if err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
		}

	// ── vol_get ────────────────────────────────────────────────────────────
	case "vol_get":
		base.OK = true
		base.Level = audio.GetVolume()

	// ── vol_set ────────────────────────────────────────────────────────────
	case "vol_set":
		level, err := audio.SetVolume(req.Level)
		if err != nil {
			log.Printf("vol_set: %v", err)
			base.Error = err.Error()
		} else {
			base.OK = true
			base.Level = level
		}

	// ── bt_detect ──────────────────────────────────────────────────────────
	case "bt_detect":
		status := bt.Detect(2)
		base.OK = true
		base.Connected = status.Connected
		base.BTDev = status.Dev
		base.BTRate = status.Rate
		base.BTChannels = status.Channels

	// ── bt_verify ──────────────────────────────────────────────────────────
	case "bt_verify":
		status := bt.Verify()
		base.OK = true
		base.Connected = status.Connected
		base.BTDev = status.Dev
		base.BTRate = status.Rate
		base.BTChannels = status.Channels

	// ── bt_scan ────────────────────────────────────────────────────────────
	case "bt_scan":
		timeout := req.Timeout
		if timeout <= 0 {
			timeout = 10
		}
		results, err := bt.Scan(timeout)
		if err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
			devs := make([]proto.BTDevice, len(results))
			for i, r := range results {
				devs[i] = proto.BTDevice{MAC: r.MAC, Name: r.Name}
			}
			base.Devices = devs
		}

	// ── bt_connect ─────────────────────────────────────────────────────────
	case "bt_connect":
		if err := bt.Connect(req.MAC); err != nil {
			base.Error = err.Error()
		} else {
			status := bt.GetStatus()
			base.OK = true
			base.Connected = status.Connected
			base.BTDev = status.Dev
			base.BTRate = status.Rate
			base.BTChannels = status.Channels
		}

	// ── bt_disconnect ──────────────────────────────────────────────────────
	case "bt_disconnect":
		if err := bt.Disconnect(req.MAC); err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
		}

	// ── bt_pair ────────────────────────────────────────────────────────────
	case "bt_pair":
		if err := bt.Pair(req.MAC); err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
		}

	// ── play ───────────────────────────────────────────────────────────────
	case "play":
		dev := req.Dev
		if dev == "" {
			dev = bt.AudioDev()
		}
		title, err := music.Play(req.Query, dev)
		if err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
			base.Title = title
		}

	// ── stop ───────────────────────────────────────────────────────────────
	case "stop":
		music.Stop()
		base.OK = true

	// ── speak_stop ─────────────────────────────────────────────────────────
	// Cancels in-progress speech. "stop" only stops music, so before this the
	// device-mode button could not interrupt Zeev at all once the daemon owned
	// playback -- there was no Python subprocess left for it to kill.
	case "speak_stop":
		audio.CancelSpeech()
		audio.StopPlayback()
		base.OK = true

	// ── record ─────────────────────────────────────────────────────────────
	case "record":
		dev := req.Dev
		if dev == "" {
			status := bt.GetStatus()
			if status.Connected {
				dev = status.Dev
			} else {
				dev = "plughw:wm8960soundcard,0" // recording uses hw directly (dmix is playback-only)
			}
		}
		wav, err := record.Record(dev, req.MaxSeconds, req.VAD, req.Rate)
		if err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
			base.WavB64 = base64.StdEncoding.EncodeToString(wav)
		}

	// ── speak_sco ──────────────────────────────────────────────────────────
	// Synthesizes text via Piper (one-shot), resamples to SCO rate, plays on
	// the SCO ALSA device.  Used as the Piper fallback in bt_speak_sco().
	case "speak_sco":
		if req.Dev == "" {
			base.Error = "speak_sco: dev (SCO device string) is required"
			break
		}
		scoRate := req.Rate
		if scoRate <= 0 {
			scoRate = 8000
		}
		if err := s.speakSCO(req.Text, req.Dev, scoRate); err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
		}

	// ── sco_record ─────────────────────────────────────────────────────────
	// Records from an SCO capture device at the negotiated rate.
	case "sco_record":
		if req.Dev == "" {
			base.Error = "sco_record: dev (SCO device string) is required"
			break
		}
		scoRate := req.Rate
		if scoRate <= 0 {
			scoRate = 8000
		}
		maxSeconds := req.MaxSeconds
		if maxSeconds <= 0 {
			maxSeconds = 8
		}
		wav, err := record.Record(req.Dev, maxSeconds, req.VAD, scoRate)
		if err != nil {
			base.Error = err.Error()
		} else {
			base.OK = true
			base.WavB64 = base64.StdEncoding.EncodeToString(wav)
		}

	// ── health ─────────────────────────────────────────────────────────────
	case "health":
		stats := health.Collect()
		base.OK = true
		// Re-use WavB64 field slot isn't clean; embed in Error as JSON for now.
		// In a future protocol version this would be a typed field.
		base.Title = fmt.Sprintf("load=%.2f/%.2f/%.2f mem=%dkB avail batt=%d%%",
			stats.Load1, stats.Load5, stats.Load15, stats.MemAvail, stats.Battery)

	default:
		base.Error = fmt.Sprintf("unknown command: %q", req.Cmd)
	}

	return base
}

// remotePiperSynth calls bosgame's TTS HTTP API (the primary/default
// backend). See remotePiperSynthAt for the multi-backend version used by
// speakPiper to split work across bosgame and a second machine.
func (s *Server) remotePiperSynth(text string, voiceOverride ...string) (pcm []byte, rate int, err error) {
	voice := s.state.RemotePiperVoice
	if len(voiceOverride) > 0 && voiceOverride[0] != "" {
		voice = voiceOverride[0]
	}
	return s.remotePiperSynthAt(s.state.RemotePiperURL, s.state.RemotePiperKey, text, voice, "")
}

// remotePiperSynthAt calls a TTS HTTP API at the given endpoint and returns
// raw PCM plus the sample rate read from the WAV header (supports both
// 22050Hz Piper and 24000Hz Kokoro without any hardcoded assumption). lang
// "ru" routes bosgame's tts_server.py to its dedicated Russian Piper model
// instead of Kokoro (which doesn't support Russian) — see server-side
// tts_server.py for the dispatch logic.
func (s *Server) remotePiperSynthAt(url, key, text, voice, lang string) (pcm []byte, rate int, err error) {
	escaped := strings.ReplaceAll(text, `"`, `\"`)
	fields := []string{`"text":"` + escaped + `"`}
	if voice != "" {
		fields = append(fields, `"voice":"`+voice+`"`)
	}
	if lang != "" {
		fields = append(fields, `"lang":"`+lang+`"`)
	}
	body := []byte("{" + strings.Join(fields, ",") + "}")
	req, err2 := http.NewRequest("POST", url, bytes.NewReader(body))
	if err2 != nil {
		return nil, 0, err2
	}
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("X-Zeev-Key", key)
	}
	resp, err2 := s.state.RemotePiperClient.Do(req)
	if err2 != nil {
		return nil, 0, fmt.Errorf("remote tts: %w", err2)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, 0, fmt.Errorf("remote tts: HTTP %d", resp.StatusCode)
	}
	wav, err2 := io.ReadAll(resp.Body)
	if err2 != nil {
		return nil, 0, fmt.Errorf("remote tts read: %w", err2)
	}
	if len(wav) < 44 {
		return nil, 0, fmt.Errorf("remote tts: response too short (%d bytes)", len(wav))
	}
	// Parse sample rate from WAV header bytes 24-27 (little-endian uint32).
	sr := int(uint32(wav[24]) | uint32(wav[25])<<8 | uint32(wav[26])<<16 | uint32(wav[27])<<24)
	if sr <= 0 {
		sr = 22050 // safe default
	}
	return wav[44:], sr, nil
}

// splitSentences splits text at ". "/"! "/"? " boundaries. Sentences shorter
// than 10 chars are merged with the next chunk to avoid false splits on
// abbreviations like "Mr." or "e.g."
//
// bosgame's Kokoro backend runs close to real-time on CPU (no GPU on that
// box) and its HTTP server is single-threaded, so unlike a fast/parallel TTS
// backend there's little slack to hide synthesis behind playback, and firing
// multiple requests concurrently just makes each one individually slower
// (measured: 3 concurrent ~2s requests each took ~5s). So we deliberately do
// NOT fragment every sentence into many small chunks — more chunks just
// means more total fixed per-request overhead with no offsetting gain. The
// one exception is the first sentence: see splitFirstClause below.
func splitSentences(text string) []string {
	var out []string
	start := 0
	for i := 1; i < len(text); i++ {
		c := text[i-1]
		if (c == '.' || c == '!' || c == '?') && text[i] == ' ' {
			s := strings.TrimSpace(text[start:i])
			if len(s) >= 10 {
				out = append(out, s)
				start = i + 1
			}
		}
	}
	if s := strings.TrimSpace(text[start:]); s != "" {
		out = append(out, s)
	}
	if len(out) == 0 {
		return []string{text}
	}
	return splitFirstClause(out)
}

// splitFirstClause breaks the first sentence at its first comma past
// clauseMinChars, if any, so the very first Kokoro request — the one that
// determines how long the listener waits in silence — is a short lead
// clause instead of the whole (often long) first sentence. Only the first
// sentence is touched; see splitSentences for why the rest stay whole.
func splitFirstClause(sentences []string) []string {
	const clauseMinChars = 40
	first := sentences[0]
	for i := 1; i < len(first); i++ {
		if first[i-1] == ',' && first[i] == ' ' && i >= clauseMinChars {
			lead := strings.TrimSpace(first[:i])
			rest := strings.TrimSpace(first[i+1:])
			if len(lead) >= 10 && len(rest) >= 10 {
				return append([]string{lead, rest}, sentences[1:]...)
			}
			break
		}
	}
	return sentences
}

func (s *Server) speakPiper(text, dev string, sync bool, voice, lang string) error {
	btStatus := bt.GetStatus()
	speechCtx := audio.BeginSpeech()

	if s.state.RemotePiperURL != "" {
		sentences := splitSentences(text)
		if len(sentences) == 0 {
			return nil
		}

		// Synthesize with at most one request in flight PER BACKEND.
		// bosgame's Kokoro server is single-threaded and runs close to
		// real-time on CPU, so multiple concurrent requests to the SAME
		// backend contend with each other and each gets slower rather than
		// actually running in parallel (measured: 3 concurrent ~2s requests
		// each took ~5s individually). But two independent machines have no
		// such contention, so when a second backend (RemotePiperURL2) is
		// configured, chunks alternate between them — each machine
		// synthesizes its half sequentially, and the two halves proceed
		// concurrently. With only one backend configured, this degrades to
		// the same fully-sequential-with-lookahead behavior as before.
		type synthResult struct {
			pcm  []byte
			rate int
			err  error
		}
		results := make([]chan synthResult, len(sentences))
		for i := range sentences {
			results[i] = make(chan synthResult, 1)
		}

		// feiergente01 (the second backend) doesn't have the Russian/Spanish
		// Piper models set up — only bosgame handles those, so skip the
		// split for them and keep every chunk on the primary backend.
		hasSecondBackend := s.state.RemotePiperURL2 != "" && lang != "ru" && lang != "es"
		synthOne := func(i int) {
			url, key := s.state.RemotePiperURL, s.state.RemotePiperKey
			if hasSecondBackend && i%2 == 1 {
				url, key = s.state.RemotePiperURL2, s.state.RemotePiperKey2
			}
			pcm, rate, err := s.remotePiperSynthAt(url, key, sentences[i], voice, lang)
			results[i] <- synthResult{pcm, rate, err}
		}
		runSequence := func(startIdx, step int) {
			for i := startIdx; i < len(sentences); i += step {
				synthOne(i)
			}
		}
		if hasSecondBackend && len(sentences) > 1 {
			go runSequence(0, 2) // bosgame: chunks 0, 2, 4, ...
			go runSequence(1, 2) // feiergente01: chunks 1, 3, 5, ...
		} else {
			go runSequence(0, 1)
		}

		// Select rather than a plain receive: this waits on the TTS backend for
		// the first chunk, and a cancel arriving in that window has no aplay to
		// kill yet. Without this the daemon sat here until the backend replied,
		// which read as "the button is slow".
		var first synthResult
		select {
		case first = <-results[0]:
		case <-speechCtx.Done():
			return audio.ErrSpeechCancelled
		}
		if first.err != nil {
			return first.err
		}
		ttsRate := first.rate

		// Determine output device/rate.
		outDev, outRate, outCh := dev, ttsRate, 1
		if btStatus.Connected {
			outDev, outRate, outCh = btStatus.Dev, btStatus.Rate, btStatus.Channels
		}

		playErr := audio.APlayPipe(outDev, "S16_LE", outRate, outCh, func(w io.Writer) error {
			writePCM := func(pcm []byte) error {
				if btStatus.Connected && (outRate != ttsRate || outCh != 1) {
					var e error
					pcm, e = resampleFFmpeg(pcm, ttsRate, 1, outRate, outCh)
					if e != nil {
						return fmt.Errorf("resample: %w", e)
					}
				}
				_, err := w.Write(pcm)
				return err
			}
			if audio.SpeechCancelled(speechCtx) {
				return audio.ErrSpeechCancelled
			}
			if err := writePCM(first.pcm); err != nil {
				return err
			}
			for i := 1; i < len(sentences); i++ {
				// Must select, not plain receive: a cancel during synthesis of
				// a later sentence would otherwise sit here until the backend
				// replied, and the reply would finish playing regardless.
				var res synthResult
				select {
				case res = <-results[i]:
				case <-speechCtx.Done():
					return audio.ErrSpeechCancelled
				}
				if res.err != nil {
					return res.err
				}
				if err := writePCM(res.pcm); err != nil {
					return err
				}
			}
			return nil
		})
		// A cancel kills aplay mid-write, which surfaces as a generic pipe
		// error; report it as cancellation so the caller does not "recover"
		// by speaking the whole reply again through espeak.
		if playErr != nil && audio.SpeechCancelled(speechCtx) {
			return audio.ErrSpeechCancelled
		}
		return playErr
	}

	// Local Piper path.
	var pcm []byte
	var err error
	ttsRate := 22050
	if s.state.PiperProc != nil {
		if btStatus.Connected {
			pcm, err = piper.SynthesizeOneShot(s.state.PiperBin, s.state.PiperModel, text)
		} else {
			pcm, err = s.state.PiperProc.Synthesize(text)
		}
	} else {
		return fmt.Errorf("no tts available (local proc nil, no remote URL)")
	}
	if err != nil {
		return err
	}
	if len(pcm) == 0 {
		return fmt.Errorf("tts returned empty audio")
	}
	if btStatus.Connected {
		pcm, err = resampleFFmpeg(pcm, ttsRate, 1, btStatus.Rate, btStatus.Channels)
		if err != nil {
			return fmt.Errorf("resample: %w", err)
		}
		return audio.APlay(pcm, btStatus.Dev, "S16_LE", btStatus.Rate, btStatus.Channels)
	}
	return audio.APlay(pcm, dev, "S16_LE", ttsRate, 1)
}

// resampleFFmpeg resamples raw S16_LE PCM via an inline ffmpeg pipeline.
func resampleFFmpeg(pcm []byte, inRate, inCh, outRate, outCh int) ([]byte, error) {
	cmd := exec.Command("ffmpeg",
		"-f", "s16le", "-ar", fmt.Sprintf("%d", inRate), "-ac", fmt.Sprintf("%d", inCh),
		"-i", "pipe:0",
		"-f", "s16le", "-ar", fmt.Sprintf("%d", outRate), "-ac", fmt.Sprintf("%d", outCh),
		"pipe:1",
	)
	cmd.Stdin = newBytesReader(pcm)
	return cmd.Output()
}

// speakSCO synthesizes text via Piper (remote or local one-shot), resamples to
// scoRate, and plays on the SCO ALSA device.
func (s *Server) speakSCO(text, scoDev string, scoRate int) error {
	var pcm []byte
	var err error
	ttsRate := 22050
	if s.state.RemotePiperURL != "" {
		var sr int
		pcm, sr, err = s.remotePiperSynth(text)
		if err != nil {
			return fmt.Errorf("remote tts SCO: %w", err)
		}
		if sr > 0 {
			ttsRate = sr
		}
	} else {
		if s.state.PiperBin == "" || s.state.PiperModel == "" {
			return fmt.Errorf("piper not found")
		}
		pcm, err = piper.SynthesizeOneShot(s.state.PiperBin, s.state.PiperModel, text)
		if err != nil {
			return fmt.Errorf("piper: %w", err)
		}
	}
	if len(pcm) == 0 {
		return fmt.Errorf("tts returned empty audio")
	}
	// Resample from TTS rate to SCO rate if needed.
	if scoRate != ttsRate {
		pcm, err = resampleFFmpeg(pcm, ttsRate, 1, scoRate, 1)
		if err != nil {
			return fmt.Errorf("resample to %dHz: %w", scoRate, err)
		}
	}
	return audio.APlay(pcm, scoDev, "S16_LE", scoRate, 1)
}

func speakEspeak(text, dev string) error {
	cmd := exec.Command("espeak-ng",
		"--stdout", text,
	)
	pcm, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("espeak-ng: %w", err)
	}
	return audio.APlay(pcm, dev, "S16_LE", 22050, 1)
}
