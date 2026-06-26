// Package piper manages a persistent Piper TTS subprocess.
// The ONNX model is loaded once at daemon startup (~20s cold on Pi Zero 2W);
// subsequent synthesis calls reuse the warm process (~1–2s per sentence).
package piper

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
)

type Proc struct {
	mu      sync.Mutex
	bin     string
	model   string
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	stdout  io.ReadCloser
	bufout  *bufio.Reader
	running bool
}

// New returns a Proc that will use the given Piper binary and ONNX model path.
// Call Start() to warm the model.
func New(bin, model string) *Proc {
	return &Proc{bin: bin, model: model}
}

// FindPiper searches common Piper install locations and returns (bin, model, ok).
func FindPiper() (bin, model string, ok bool) {
	home, _ := os.UserHomeDir()
	candidates := []string{
		filepath.Join(home, "piper", "piper"),
		filepath.Join(home, ".local", "bin", "piper"),
		"/usr/local/bin/piper",
	}
	for _, b := range candidates {
		if _, err := os.Stat(b); err == nil {
			bin = b
			break
		}
	}
	if bin == "" {
		return "", "", false
	}

	modelDirs := []string{
		filepath.Join(home, "piper"),
		filepath.Join(home, ".local", "share", "piper"),
	}
	for _, dir := range modelDirs {
		entries, err := filepath.Glob(filepath.Join(dir, "*.onnx"))
		if err != nil || len(entries) == 0 {
			continue
		}
		for _, e := range entries {
			if strings.Contains(e, "en_US") || strings.Contains(e, "lessac") {
				model = e
				break
			}
		}
		if model == "" && len(entries) > 0 {
			model = entries[0]
		}
		if model != "" {
			break
		}
	}
	if model == "" {
		return "", "", false
	}
	return bin, model, true
}

// Start launches the persistent Piper process. Idempotent.
func (p *Proc) Start() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.running {
		return nil
	}
	return p.start()
}

func (p *Proc) start() error {
	cmd := exec.Command(p.bin,
		"--model", p.model,
		"--output-raw",
	)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("piper stdin: %w", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("piper stdout: %w", err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("piper start: %w", err)
	}
	p.cmd = cmd
	p.stdin = stdin
	p.stdout = stdout
	p.bufout = bufio.NewReader(stdout)
	p.running = true
	log.Printf("piper: started pid %d model=%s", cmd.Process.Pid, filepath.Base(p.model))
	return nil
}

// Synthesize writes text to Piper and collects the raw PCM output.
// Uses the persistent process; restarts once on broken pipe.
func (p *Proc) Synthesize(text string) ([]byte, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	if !p.running {
		if err := p.start(); err != nil {
			return nil, err
		}
	}

	pcm, err := p.synth(text)
	if err != nil {
		// Retry once after process reset.
		log.Printf("piper: synth error (%v), restarting", err)
		p.kill()
		if err2 := p.start(); err2 != nil {
			return nil, fmt.Errorf("piper restart: %w", err2)
		}
		return p.synth(text)
	}
	return pcm, nil
}

func (p *Proc) synth(text string) ([]byte, error) {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, nil
	}
	if _, err := fmt.Fprintln(p.stdin, text); err != nil {
		return nil, fmt.Errorf("piper write: %w", err)
	}
	// Piper writes raw PCM; we read until no bytes arrive for idleTimeout.
	// Because --output-raw doesn't delimit sentences, we use a simple
	// read-all-available approach: drain stdout with a short deadline.
	// The process stays alive; we close nothing.
	var buf bytes.Buffer
	tmp := make([]byte, 4096)
	for {
		n, err := p.stdout.Read(tmp)
		if n > 0 {
			buf.Write(tmp[:n])
		}
		if err != nil {
			if err == io.EOF {
				break
			}
			return nil, fmt.Errorf("piper read: %w", err)
		}
		// If the read returned fewer bytes than the buffer, we're likely
		// at the end of this sentence's output — but we can't know for sure
		// without a sentinel. Piper outputs a newline after each sentence
		// when using --output-raw + JSON output; without JSON we just read
		// until idle. This is handled via the one-shot path for BT.
		if n < len(tmp) {
			break
		}
	}
	return buf.Bytes(), nil
}

// SynthesizeOneShot runs a fresh Piper subprocess for one text block and
// returns all PCM bytes. Used for BT output where we need the full block.
func SynthesizeOneShot(bin, model, text string) ([]byte, error) {
	cmd := exec.Command(bin, "--model", model, "--output-raw")
	cmd.Stderr = os.Stderr
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	fmt.Fprintln(stdin, text)
	stdin.Close()
	pcm, err := io.ReadAll(stdout)
	if werr := cmd.Wait(); werr != nil && err == nil {
		err = werr
	}
	return pcm, err
}

func (p *Proc) kill() {
	if p.cmd != nil && p.cmd.Process != nil {
		p.cmd.Process.Kill()
		p.cmd.Wait()
	}
	p.running = false
	p.cmd = nil
	p.stdin = nil
	p.stdout = nil
	p.bufout = nil
}

// Stop shuts down the persistent Piper process.
func (p *Proc) Stop() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.kill()
}
