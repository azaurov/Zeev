package audio

import (
	"context"
	"log"
	"os/exec"
	"time"
)

// keepaliveTimeout bounds a single aplay. The buffer is one second long, so
// anything approaching this is already wedged.
const keepaliveTimeout = 10 * time.Second

// StartKeepalive plays a 1s silent buffer every 20s to keep the WM8960
// from auto-powering down. Stops when ctx is cancelled.
func StartKeepalive(ctx context.Context, devFn func() string) {
	go func() {
		ticker := time.NewTicker(20 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				// EVERY aplay is bounded. It used to inherit only the daemon's
				// lifetime context, which is never cancelled while running, so
				// one hung aplay blocked cmd.Wait() forever and took the whole
				// keepalive goroutine with it. Observed on bosgame wedged from
				// 29 Jul to 2 Aug -- four days, on a box with no WM8960 -- and
				// it logged NOTHING the entire time, because the goroutine was
				// stuck rather than erroring. The watchdog cannot catch this
				// either: it self-dials `health`, which keeps answering
				// perfectly while the keepalive is dead. Killing the stray
				// aplay by hand unblocked it and it resumed ticking at once.
				keepaliveOnce(ctx, devFn())
			}
		}
	}()
}

func keepaliveOnce(parent context.Context, dev string) {
	ctx, cancel := context.WithTimeout(parent, keepaliveTimeout)
	defer cancel()

	// 1s of 16-bit stereo silence at 44100 Hz = 44100*2*2 bytes
	silence := make([]byte, 44100*2*2)
	cmd := exec.CommandContext(ctx, "aplay",
		"-D", dev,
		"-f", "S16_LE",
		"-r", "44100",
		"-c", "2",
	)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		log.Printf("keepalive: stdin pipe: %v", err)
		return
	}
	if err := cmd.Start(); err != nil {
		log.Printf("keepalive: aplay start: %v", err)
		return
	}
	// A stuck reader blocks this write once the pipe buffer fills; the context
	// killing aplay closes the pipe and fails it, which is the point.
	stdin.Write(silence)
	stdin.Close()
	if err := cmd.Wait(); err != nil {
		// Non-fatal: device may have been swapped to BT.
		log.Printf("keepalive: aplay: %v", err)
	}
	if ctx.Err() == context.DeadlineExceeded {
		log.Printf("keepalive: aplay exceeded %s on %s — killed", keepaliveTimeout, dev)
	}
}
