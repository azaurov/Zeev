// Package music handles YouTube playback via yt-dlp → ffmpeg → aplay.
package music

import (
	"context"
	"fmt"
	"log"
	"os/exec"
	"strings"
	"sync"
)

var (
	mu      sync.Mutex
	cancel  context.CancelFunc
	playing string
)

// Play searches YouTube for query, downloads best audio, and pipes through
// ffmpeg → aplay on dev. Returns the video title.
// Cancels any in-progress playback before starting.
func Play(query, dev string) (string, error) {
	Stop()

	ctx, cancelFn := context.WithCancel(context.Background())
	mu.Lock()
	cancel = cancelFn
	mu.Unlock()

	// Resolve the audio URL via yt-dlp.
	urlCmd := exec.CommandContext(ctx, "yt-dlp",
		"--default-search", "ytsearch1",
		"--get-url",
		"--format", "bestaudio",
		query,
	)
	urlOut, err := urlCmd.Output()
	if err != nil {
		cancelFn()
		return "", fmt.Errorf("yt-dlp URL: %w", err)
	}
	audioURL := strings.TrimSpace(string(urlOut))

	// Resolve title separately.
	titleCmd := exec.Command("yt-dlp",
		"--default-search", "ytsearch1",
		"--get-title",
		query,
	)
	titleOut, _ := titleCmd.Output()
	title := strings.TrimSpace(string(titleOut))
	if title == "" {
		title = query
	}

	mu.Lock()
	playing = title
	mu.Unlock()

	log.Printf("music: playing %q via %s", title, dev)

	// ffmpeg decodes the stream; aplay outputs to the ALSA device.
	go func() {
		defer func() {
			mu.Lock()
			playing = ""
			mu.Unlock()
			cancelFn()
		}()

		ffmpeg := exec.CommandContext(ctx, "ffmpeg",
			"-i", audioURL,
			"-f", "s16le",
			"-ar", "44100",
			"-ac", "2",
			"pipe:1",
		)
		aplay := exec.CommandContext(ctx, "aplay",
			"-D", dev,
			"-f", "S16_LE",
			"-r", "44100",
			"-c", "2",
		)

		pipe, err := ffmpeg.StdoutPipe()
		if err != nil {
			log.Printf("music: ffmpeg stdout: %v", err)
			return
		}
		aplay.Stdin = pipe

		if err := ffmpeg.Start(); err != nil {
			log.Printf("music: ffmpeg start: %v", err)
			return
		}
		if err := aplay.Start(); err != nil {
			log.Printf("music: aplay start: %v", err)
			ffmpeg.Process.Kill()
			return
		}
		ffmpeg.Wait()
		aplay.Wait()
	}()

	return title, nil
}

// Stop cancels any in-progress playback.
func Stop() {
	mu.Lock()
	fn := cancel
	cancel = nil
	playing = ""
	mu.Unlock()
	if fn != nil {
		fn()
	}
}

// NowPlaying returns the currently playing title, or "".
func NowPlaying() string {
	mu.Lock()
	defer mu.Unlock()
	return playing
}
