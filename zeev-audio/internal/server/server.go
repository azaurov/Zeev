package server

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"log"
	"net"
	"os"

	"github.com/azaurov/zeev-audio/internal/audio"
	"github.com/azaurov/zeev-audio/internal/bt"
	"github.com/azaurov/zeev-audio/internal/piper"
	"github.com/azaurov/zeev-audio/internal/proto"
)

// Server is the Unix-socket daemon.
type Server struct {
	socketPath string
	listener   net.Listener
	state      *State
	ctx        context.Context
	cancel     context.CancelFunc
}

// New creates a Server. Call Run() to start accepting connections.
func New(socketPath string) (*Server, error) {
	os.Remove(socketPath)
	l, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, err
	}
	os.Chmod(socketPath, 0660)

	ctx, cancel := context.WithCancel(context.Background())
	s := &Server{
		socketPath: socketPath,
		listener:   l,
		state:      &State{},
		ctx:        ctx,
		cancel:     cancel,
	}
	return s, nil
}

// Init sets up Piper, detects BT, starts keepalive. Called before Run().
func (s *Server) Init() {
	// Remote Piper TTS on bosgame takes priority over local Piper process.
	// Set REMOTE_PIPER_URL (and optionally REMOTE_PIPER_KEY) in the systemd unit.
	s.state.RemotePiperURL   = os.Getenv("REMOTE_PIPER_URL")
	s.state.RemotePiperKey   = os.Getenv("REMOTE_PIPER_KEY")
	s.state.RemotePiperVoice = os.Getenv("REMOTE_PIPER_VOICE")

	if s.state.RemotePiperURL != "" {
		log.Printf("piper: using remote TTS at %s (voice=%q)", s.state.RemotePiperURL, s.state.RemotePiperVoice)
	} else {
		// Try to find and start local Piper.
		bin, model, ok := piper.FindPiper()
		if ok {
			s.state.PiperBin = bin
			s.state.PiperModel = model
			p := piper.New(bin, model)
			if err := p.Start(); err != nil {
				log.Printf("piper: startup failed: %v (will retry on first speak)", err)
			} else {
				s.state.PiperProc = p
			}
		} else {
			log.Println("piper: not found — espeak-ng fallback only")
		}
	}

	// Detect BT headphones (optional at boot; retry 2×).
	bt.Detect(2)

	// Start WM8960 keepalive.
	audio.StartKeepalive(s.ctx, bt.AudioDev)

	log.Printf("zeev-audio: listening on %s", s.socketPath)
}

// Run accepts connections until the context is cancelled.
func (s *Server) Run() {
	for {
		conn, err := s.listener.Accept()
		if err != nil {
			select {
			case <-s.ctx.Done():
				return
			default:
				log.Printf("accept: %v", err)
				continue
			}
		}
		go s.handleConn(conn)
	}
}

// Stop shuts down the server.
func (s *Server) Stop() {
	s.cancel()
	s.listener.Close()
	if s.state.PiperProc != nil {
		s.state.PiperProc.Stop()
	}
	os.Remove(s.socketPath)
}

func (s *Server) handleConn(conn net.Conn) {
	defer conn.Close()
	reader := bufio.NewReader(conn)
	writer := bufio.NewWriter(conn)

	for {
		req, err := proto.ReadRequest(reader)
		if err != nil {
			if err != io.EOF {
				log.Printf("read: %v", err)
			}
			return
		}
		log.Printf("cmd=%s id=%s", req.Cmd, req.ID)

		resp := s.handle(req)
		if err := proto.WriteResponse(writer, resp); err != nil {
			log.Printf("write: %v", err)
			return
		}
		writer.Flush()
	}
}

// newBytesReader wraps a []byte in an io.Reader (used by handlers.go).
func newBytesReader(b []byte) io.Reader {
	return bytes.NewReader(b)
}
