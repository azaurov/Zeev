// Package music handles YouTube playback via yt-dlp → ffmpeg → aplay.
package music

import (
	"context"
	"fmt"
	"log"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// ffmpegArgs decodes the resolved stream to raw PCM. The -reconnect flags are
// load-bearing: without them a dropped or stalled googlevideo connection ends
// ffmpeg cleanly mid-song and the track just stops, with nothing in the log.
func ffmpegArgs(audioURL string) []string {
	return []string{
		"-reconnect", "1",
		"-reconnect_streamed", "1",
		"-reconnect_delay_max", "5",
		"-i", audioURL,
		"-f", "s16le",
		"-ar", "44100",
		"-ac", "2",
		"pipe:1",
	}
}

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

		ffmpeg := exec.CommandContext(ctx, "ffmpeg", ffmpegArgs(audioURL)...)
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
		// Exit status and elapsed time are logged so a track that stops early can
		// be told apart from one that finished: a Wait error with the context
		// still live means ffmpeg/aplay died on their own, not that Stop() ran.
		began := time.Now()
		fErr := ffmpeg.Wait()
		aErr := aplay.Wait()
		log.Printf("music: %q ended after %s (ffmpeg: %v, aplay: %v, cancelled: %t)",
			title, time.Since(began).Round(time.Second), fErr, aErr, ctx.Err() != nil)
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
