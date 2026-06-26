package audio

import (
	"context"
	"log"
	"os/exec"
	"time"
)

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
				dev := devFn()
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
					continue
				}
				if err := cmd.Start(); err != nil {
					log.Printf("keepalive: aplay start: %v", err)
					continue
				}
				stdin.Write(silence)
				stdin.Close()
				if err := cmd.Wait(); err != nil {
					// Non-fatal: device may have been swapped to BT.
					log.Printf("keepalive: aplay: %v", err)
				}
			}
		}
	}()
}
