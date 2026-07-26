// Package sdnotify sends systemd service notifications (READY=1,
// WATCHDOG=1) over the NOTIFY_SOCKET datagram socket. No-ops cleanly
// when not running under systemd (NOTIFY_SOCKET unset), so it's safe
// to call unconditionally in dev/non-systemd environments.
package sdnotify

import (
	"net"
	"os"
	"strconv"
	"time"
)

// Notify sends state (e.g. "READY=1", "WATCHDOG=1") to systemd.
// Returns nil silently if NOTIFY_SOCKET is not set.
func Notify(state string) error {
	addr := os.Getenv("NOTIFY_SOCKET")
	if addr == "" {
		return nil
	}
	conn, err := net.Dial("unixgram", addr)
	if err != nil {
		return err
	}
	defer conn.Close()
	_, err = conn.Write([]byte(state))
	return err
}

// WatchdogInterval returns the ping interval systemd expects (half of
// WATCHDOG_USEC, the systemd-recommended margin) and whether the
// watchdog is enabled for this unit at all.
func WatchdogInterval() (time.Duration, bool) {
	usec := os.Getenv("WATCHDOG_USEC")
	if usec == "" {
		return 0, false
	}
	n, err := strconv.ParseInt(usec, 10, 64)
	if err != nil || n <= 0 {
		return 0, false
	}
	return time.Duration(n) * time.Microsecond / 2, true
}
