package audio

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strconv"
	"strings"
	"sync"
)

var (
	volMu  sync.Mutex
	volume = 94
	// aplayMu serializes all aplay calls so the keepalive goroutine and TTS
	// cannot open the ALSA device simultaneously (causes broken pipe errors).
	aplayMu sync.Mutex

	// playMu guards the in-flight aplay process so StopPlayback can kill it.
	// Deliberately NOT aplayMu: that one is held for the whole duration of
	// playback, so reusing it would make StopPlayback block until the audio
	// it is trying to cancel had already finished.
	playMu  sync.Mutex
	playCmd *exec.Cmd

	// speechCtx scopes one speak request. Killing aplay is not sufficient on
	// its own: a multi-sentence reply feeds a single aplay from a loop that
	// blocks waiting for later sentences to come back from the TTS backend, so
	// a cancel arriving mid-synthesis has nothing to kill and the loop keeps
	// waiting. Callers select on Done() to break out of that wait.
	speechMu     sync.Mutex
	speechCtx    context.Context
	speechCancel context.CancelFunc
)

// ErrSpeechCancelled marks a speak aborted by speak_stop. Callers must treat
// it as success: it is a deliberate interruption, not a synthesis failure, and
// retrying through a fallback engine would re-speak the whole reply.
var ErrSpeechCancelled = errors.New("speech cancelled")

// BeginSpeech opens a new cancellable scope for one speak request,
// superseding any previous one.
func BeginSpeech() context.Context {
	speechMu.Lock()
	defer speechMu.Unlock()
	if speechCancel != nil {
		speechCancel()
	}
	speechCtx, speechCancel = context.WithCancel(context.Background())
	return speechCtx
}

// CancelSpeech aborts the current speech scope.
func CancelSpeech() {
	speechMu.Lock()
	c := speechCancel
	speechMu.Unlock()
	if c != nil {
		c()
	}
}

// SpeechCancelled reports whether the given scope has been cancelled.
func SpeechCancelled(ctx context.Context) bool {
	return ctx != nil && ctx.Err() != nil
}

// setPlaying records the in-flight aplay process, or clears it with nil.
func setPlaying(cmd *exec.Cmd) {
	playMu.Lock()
	playCmd = cmd
	playMu.Unlock()
}

// StopPlayback kills any in-progress speech playback and latches a cancelled
// flag, so a multi-sentence reply stops instead of advancing to the next
// chunk. Returns true if something was actually playing.
func StopPlayback() bool {
	CancelSpeech()
	playMu.Lock()
	cmd := playCmd
	playCmd = nil
	playMu.Unlock()
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
		return true
	}
	return false
}

// GetVolume returns the current volume (0–100).
func GetVolume() int {
	volMu.Lock()
	defer volMu.Unlock()
	return volume
}

// SetVolume sets the system volume (0–100) via amixer and returns the clamped level.
func SetVolume(level int) (int, error) {
	if level < 0 {
		level = 0
	}
	if level > 100 {
		level = 100
	}

	// Try Master control first; fall back to card-specific Speaker.
	cmd := exec.Command("amixer", "sset", "Master", fmt.Sprintf("%d%%", level))
	if err := cmd.Run(); err != nil {
		cmd2 := exec.Command("amixer", "-c", "wm8960soundcard", "sset", "Speaker",
			strconv.Itoa(level*127/100))
		if err2 := cmd2.Run(); err2 != nil {
			return level, fmt.Errorf("amixer Master: %w; amixer Speaker: %v", err, err2)
		}
	}

	volMu.Lock()
	volume = level
	volMu.Unlock()
	return level, nil
}

// APlay pipes raw PCM data to aplay on the given device.
// Acquires aplayMu so the keepalive goroutine and TTS never open the device
// simultaneously (concurrent opens cause broken pipe on the WM8960).
func APlay(pcmData []byte, dev, format string, rate, channels int) error {
	if format == "" {
		format = "S16_LE"
	}
	aplayMu.Lock()
	defer aplayMu.Unlock()
	args := []string{
		"-D", dev,
		"-f", format,
		"-r", strconv.Itoa(rate),
		"-c", strconv.Itoa(channels),
	}
	cmd := exec.Command("aplay", args...)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	setPlaying(cmd)
	defer setPlaying(nil)
	if _, err := stdin.Write(pcmData); err != nil {
		stdin.Close()
		cmd.Wait()
		return err
	}
	stdin.Close()
	return cmd.Wait()
}

// APlayPipe opens aplay once and calls feed() to write all PCM data, then
// waits for playback to finish. Holding aplayMu for the whole duration
// prevents the keepalive from opening the device mid-stream.
// Use this when playing multiple sequential chunks (e.g. sentence-by-sentence
// TTS) to avoid the WM8960 glitch caused by rapid open/close cycles.
func APlayPipe(dev, format string, rate, channels int, feed func(w io.Writer) error) error {
	if format == "" {
		format = "S16_LE"
	}
	aplayMu.Lock()
	defer aplayMu.Unlock()
	cmd := exec.Command("aplay",
		"-D", dev,
		"-f", format,
		"-r", strconv.Itoa(rate),
		"-c", strconv.Itoa(channels),
	)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	setPlaying(cmd)
	defer setPlaying(nil)
	feedErr := feed(stdin)
	stdin.Close()
	waitErr := cmd.Wait()
	if feedErr != nil {
		return feedErr
	}
	return waitErr
}

// DefaultSpeakerDev returns the wired speaker ALSA device.
// Uses "default" (dmix) instead of "plughw:" so the daemon shares the
// WM8960 with other ALSA clients (mpg123, espeak-ng) without exclusive lock.
func DefaultSpeakerDev() string {
	return "default"
}

// ParseBTDev extracts the ALSA device from a bluealsa PCM string of the form
// "bluealsa:DEV=XX:XX:XX:XX:XX:XX,PROFILE=a2dp,...".
// Returns the input unchanged if it doesn't start with "bluealsa".
func ParseBTDev(s string) string {
	return strings.TrimSpace(s)
}
