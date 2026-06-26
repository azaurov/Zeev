package main

import (
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

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
