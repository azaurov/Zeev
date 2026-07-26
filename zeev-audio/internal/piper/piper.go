// Package piper manages a persistent Piper TTS subprocess.
// The ONNX model is loaded once at daemon startup (~20s cold on Pi Zero 2W);
// subsequent synthesis calls reuse the warm process (~1–2s per sentence).
package piper

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
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
		info, err := os.Stat(b)
		if err == nil && !info.IsDir() {
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

// firstChunkTimeout is how long to wait for the first PCM byte from Piper.
// Covers cold ONNX model load (~20s) and warm synthesis start (~1-2s).
const firstChunkTimeout = 30 * time.Second

// idleTimeout is how long to wait between PCM bursts before declaring done.
// Measured inter-sentence gap on warm Pi Zero 2W: ~4.5s; 8s gives 3.5s margin.
const idleTimeout = 8 * time.Second

type readResult struct {
	n   int
	err error
}

func (p *Proc) synth(text string) ([]byte, error) {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, nil
	}
	if _, err := fmt.Fprintln(p.stdin, text); err != nil {
		return nil, fmt.Errorf("piper write: %w", err)
	}

	// Read PCM with goroutine-based timeouts:
	//   - firstChunkTimeout (30s) for the initial burst
	//   - idleTimeout (8s) between subsequent bursts
	// This is more reliable than the short-read heuristic on a loaded Pi Zero 2W
	// where reads often return < bufSize mid-stream due to OS scheduling.
	var buf bytes.Buffer
	tmp := make([]byte, 4096)
	gotFirst := false
	deadline := time.Now().Add(firstChunkTimeout)

	for {
		ch := make(chan readResult, 1)
		go func() {
			n, err := p.stdout.Read(tmp)
			ch <- readResult{n, err}
		}()

		wait := idleTimeout
		if !gotFirst {
			wait = time.Until(deadline)
			if wait <= 0 {
				return nil, fmt.Errorf("piper: first chunk timeout after %v", firstChunkTimeout)
			}
		}

		select {
		case r := <-ch:
			if r.n > 0 {
				buf.Write(tmp[:r.n])
				gotFirst = true
			}
			if r.err == io.EOF {
				return buf.Bytes(), nil
			}
			if r.err != nil {
				return nil, fmt.Errorf("piper read: %w", r.err)
			}
		case <-time.After(wait):
			if !gotFirst {
				return nil, fmt.Errorf("piper: first chunk timeout after %v", firstChunkTimeout)
			}
			// idleTimeout elapsed after receiving data — synthesis complete.
			return buf.Bytes(), nil
		}
	}
}

// oneShotTimeout bounds a fresh Piper subprocess end-to-end (cold ONNX
// load ~20s plus synthesis of the whole text block). Unlike the
// persistent process's synth(), there's no idle-chunk heuristic here —
// one call, one read to EOF — so a hung process (bad model state,
// pathological input) would otherwise block the calling goroutine and
// leak the process indefinitely.
const oneShotTimeout = 60 * time.Second

// SynthesizeOneShot runs a fresh Piper subprocess for one text block and
// returns all PCM bytes. Used for BT output where we need the full block.
func SynthesizeOneShot(bin, model, text string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), oneShotTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, bin, "--model", model, "--output-raw")
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
	if ctx.Err() == context.DeadlineExceeded {
		return nil, fmt.Errorf("piper one-shot: timed out after %s", oneShotTimeout)
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
