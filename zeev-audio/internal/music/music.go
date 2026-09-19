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

// tailBuffer keeps the last max bytes written to it. ffmpeg and aplay stderr
// used to be discarded, so a track that died on its own left "exit status 1"
// and nothing to say why (the Macarena, 2026-09-19).
type tailBuffer struct {
	mu  sync.Mutex
	max int
	b   []byte
}

func (t *tailBuffer) Write(p []byte) (int, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.b = append(t.b, p...)
	if len(t.b) > t.max {
		t.b = t.b[len(t.b)-t.max:]
	}
	return len(p), nil
}

// String returns the tail as a single line, since it lands in a journal entry.
func (t *tailBuffer) String() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return strings.Join(strings.Fields(string(t.b)), " ")
}

// ytdlpBinary prefers yt-dlp-fast (scripts/yt-dlp-fast, installed beside
// yt-dlp on the Pi), which skips the ~6s zipapp startup, and falls back to plain
// yt-dlp anywhere it is not installed.
func ytdlpBinary() string {
	if _, err := exec.LookPath("yt-dlp-fast"); err == nil {
		return "yt-dlp-fast"
	}
	return "yt-dlp"
}

// ffmpegArgs decodes the resolved stream to raw PCM. The -reconnect flags are
// load-bearing: without them a dropped or stalled googlevideo connection ends
// ffmpeg cleanly mid-song and the track just stops, with nothing in the log.
func ffmpegArgs(audioURL string) []string {
	return []string{
		// Warnings and errors only: the default banner and per-second progress
		// would fill the stderr tail with noise instead of the failure.
		"-hide_banner", "-loglevel", "warning", "-nostats",
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

// parseResolve splits `yt-dlp --get-title --get-url` output: the title line
// comes first, the stream URL is the last http(s) line. A missing title falls
// back to the query, as it always did; a missing URL is an error, because
// starting ffmpeg on nothing would announce "Playing X" over silence.
func parseResolve(out, query string) (title, audioURL string, err error) {
	var lines []string
	for _, l := range strings.Split(out, "\n") {
		if l = strings.TrimSpace(l); l != "" {
			lines = append(lines, l)
		}
	}
	for i := len(lines) - 1; i >= 0; i-- {
		if strings.HasPrefix(lines[i], "http://") || strings.HasPrefix(lines[i], "https://") {
			audioURL = lines[i]
			lines = lines[:i]
			break
		}
	}
	if audioURL == "" {
		return "", "", fmt.Errorf("yt-dlp returned no stream URL")
	}
	title = query
	if len(lines) > 0 {
		title = lines[0]
	}
	return title, audioURL, nil
}

// Play searches YouTube for query, downloads best audio, and pipes through
// ffmpeg → aplay on dev. Returns the video title.
// Cancels any in-progress playback before starting.
func Play(query, dev string) (string, error) {
	Stop()

	ctx, cancelFn := context.WithCancel(context.Background())
	mu.Lock()
	cancel = cancelFn
	mu.Unlock()

	// One yt-dlp run for both title and URL. It used to be two sequential runs
	// (--get-url, then --get-title), each repeating the whole search and
	// extraction: measured on the Pi, 22s + 25s = 47s against 20s combined.
	resolveCmd := exec.CommandContext(ctx, ytdlpBinary(),
		"--default-search", "ytsearch1",
		"--get-title",
		"--get-url",
		"--format", "bestaudio",
		query,
	)
	out, err := resolveCmd.Output()
	if err != nil {
		cancelFn()
		return "", fmt.Errorf("yt-dlp: %w", err)
	}
	title, audioURL, err := parseResolve(string(out), query)
	if err != nil {
		cancelFn()
		return "", err
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

		fErrBuf, aErrBuf := &tailBuffer{max: 400}, &tailBuffer{max: 400}
		ffmpeg.Stderr, aplay.Stderr = fErrBuf, aErrBuf

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
		log.Printf("music: %q ended after %s (ffmpeg: %v, aplay: %v, cancelled: %t) ffmpeg stderr: %q aplay stderr: %q",
			title, time.Since(began).Round(time.Second), fErr, aErr, ctx.Err() != nil,
			fErrBuf.String(), aErrBuf.String())
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
