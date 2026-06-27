package audio

import (
	"fmt"
	"io"
	"os/exec"
	"strconv"
	"strings"
	"sync"
)

var (
	volMu  sync.Mutex
	volume = 87
	// aplayMu serializes all aplay calls so the keepalive goroutine and TTS
	// cannot open the ALSA device simultaneously (causes broken pipe errors).
	aplayMu sync.Mutex
)

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
