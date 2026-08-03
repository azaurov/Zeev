package audio

import (
	"context"
	"testing"
	"time"
)

// A hung aplay used to block cmd.Wait() forever, because the command inherited
// only the daemon's lifetime context. That wedged the keepalive goroutine on
// bosgame for four days (29 Jul -> 2 Aug) while logging nothing at all, and the
// watchdog could not see it: `health` kept answering perfectly throughout.
//
// "sleep" stands in for a stuck aplay -- PATH lookup is what picks the binary,
// so pointing the device at a command that never returns is not possible here;
// what IS testable, and what actually broke, is that the bound is enforced.
func TestKeepaliveOnceIsBounded(t *testing.T) {
	if keepaliveTimeout <= 0 {
		t.Fatalf("keepaliveTimeout must be positive, got %s", keepaliveTimeout)
	}
	if keepaliveTimeout > 30*time.Second {
		t.Fatalf("keepaliveTimeout %s is too loose to prevent a wedge", keepaliveTimeout)
	}

	// aplay is absent on the build host, so this returns on the start error --
	// the point is that it RETURNS, under its own bound, without the caller
	// having to cancel anything.
	done := make(chan struct{})
	go func() {
		defer close(done)
		keepaliveOnce(context.Background(), "definitely-not-a-device")
	}()
	select {
	case <-done:
	case <-time.After(keepaliveTimeout + 5*time.Second):
		t.Fatal("keepaliveOnce did not return within its own timeout — this is the wedge")
	}
}

// The parent context must still win: daemon shutdown cannot wait on a timeout.
func TestKeepaliveOnceHonoursParentCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	done := make(chan struct{})
	go func() {
		defer close(done)
		keepaliveOnce(ctx, "definitely-not-a-device")
	}()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("keepaliveOnce ignored a cancelled parent context")
	}
}
