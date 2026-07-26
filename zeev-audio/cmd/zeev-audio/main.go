package main

import (
	"bufio"
	"flag"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/azaurov/zeev-audio/internal/sdnotify"
	"github.com/azaurov/zeev-audio/internal/server"
)

func main() {
	socketPath := flag.String("socket", "/tmp/zeev-audio.sock", "Unix socket path")
	flag.Parse()

	log.SetFlags(log.Ltime | log.Lshortfile)
	log.SetOutput(os.Stderr)

	srv, err := server.New(*socketPath)
	if err != nil {
		log.Fatalf("server: %v", err)
	}

	srv.Init()
	sdnotify.Notify("READY=1")
	startWatchdog(*socketPath)

	// Catch SIGTERM / SIGINT for clean shutdown.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		<-sigs
		log.Println("shutting down")
		srv.Stop()
	}()

	srv.Run()
}

// startWatchdog pings systemd's watchdog only after confirming the
// daemon can still service a request end-to-end (accept -> dispatch ->
// respond) — a self-dial "health" round trip, not just a bare timer.
// A handler goroutine wedged on some future blocking call (the class of
// bug the 10s bluetoothctl timeout fixed for BT specifically) will make
// this check time out too, so systemd restarts a livelocked daemon
// instead of leaving it running degraded. No-ops if WatchdogSec isn't
// set on the unit.
func startWatchdog(socketPath string) {
	interval, ok := sdnotify.WatchdogInterval()
	if !ok {
		return
	}
	go func() {
		for {
			time.Sleep(interval)
			if selfCheck(socketPath) {
				sdnotify.Notify("WATCHDOG=1")
			} else {
				log.Println("watchdog: self-check failed, skipping ping")
			}
		}
	}()
}

func selfCheck(socketPath string) bool {
	conn, err := net.DialTimeout("unix", socketPath, 3*time.Second)
	if err != nil {
		return false
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(5 * time.Second))
	if _, err := conn.Write([]byte(`{"id":"watchdog","cmd":"health"}` + "\n")); err != nil {
		return false
	}
	line, err := bufio.NewReader(conn).ReadString('\n')
	if err != nil {
		return false
	}
	return len(line) > 0
}
