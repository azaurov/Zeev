package server

import (
	"bytes"
	"encoding/base64"
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
	PiperProc      *piper.Proc
	PiperBin       string
	PiperModel     string
	RemotePiperURL string // e.g. https://ollama.sogdiana-gematria.net/piper/tts
	RemotePiperKey string // X-Zeev-Key value
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
		if (s.state.RemotePiperURL != "" || s.state.PiperProc != nil) && (req.Lang == "" || req.Lang == "en") {
			err = s.speakPiper(req.Text, dev, req.Cmd == "speak_sync")
		} else {
			err = fmt.Errorf("piper not available; use espeak-ng fallback")
		}
		if err != nil {
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

	// ── record ─────────────────────────────────────────────────────────────
	case "record":
		dev := req.Dev
		if dev == "" {
			status := bt.GetStatus()
			if status.Connected {
				dev = status.Dev
			} else {
				dev = "plughw:wm8960soundcard,0"
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

// remotePiperSynth calls the bosgame TTS HTTP API and returns raw PCM plus
// the sample rate read from the WAV header (supports both 22050Hz Piper and
// 24000Hz Kokoro without any hardcoded assumption).
func (s *Server) remotePiperSynth(text string) (pcm []byte, rate int, err error) {
	body := []byte(`{"text":` + `"` + strings.ReplaceAll(text, `"`, `\"`) + `"` + `}`)
	req, err2 := http.NewRequest("POST", s.state.RemotePiperURL, bytes.NewReader(body))
	if err2 != nil {
		return nil, 0, err2
	}
	req.Header.Set("Content-Type", "application/json")
	if s.state.RemotePiperKey != "" {
		req.Header.Set("X-Zeev-Key", s.state.RemotePiperKey)
	}
	client := &http.Client{Timeout: 45 * time.Second}
	resp, err2 := client.Do(req)
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

func (s *Server) speakPiper(text, dev string, sync bool) error {
	btStatus := bt.GetStatus()
	var pcm []byte
	var err error

	ttsRate := 22050 // default for local Piper
	if s.state.RemotePiperURL != "" {
		// Remote synthesis on bosgame — frees Pi RAM and CPU.
		var sr int
		pcm, sr, err = s.remotePiperSynth(text)
		if sr > 0 {
			ttsRate = sr
		}
	} else if s.state.PiperProc != nil {
		if btStatus.Connected {
			// One-shot for BT: capture full multi-sentence output before playback.
			pcm, err = piper.SynthesizeOneShot(s.state.PiperBin, s.state.PiperModel, text)
		} else {
			// Persistent process for wired speaker.
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

	// Resample for BT if rate differs from TTS output rate.
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
