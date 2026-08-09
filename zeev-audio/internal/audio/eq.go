package audio

import (
	"encoding/binary"
	"math"
	"sync"
)

// EQBands is the number of equalizer bands published to Python for the LCD
// visualizer (see zeev.py's face_scroll "speaking" state).
const EQBands = 8

// eqFreqs are log-spaced center frequencies (Hz) covering the voice band,
// mirrored loosely against zeev.py's own log-spaced FFT-bucket bands in
// _play_pcm_chunked. Only 8 bins are needed, so a Goertzel filter per band
// is cheaper than a full FFT and needs no external dependency.
var eqFreqs = logSpace(120, 4000, EQBands)

func logSpace(lo, hi float64, n int) []float64 {
	out := make([]float64, n)
	ratio := hi / lo
	for i := 0; i < n; i++ {
		out[i] = lo * math.Pow(ratio, float64(i)/float64(n-1))
	}
	return out
}

type eqState struct {
	mu      sync.Mutex
	levels  [EQBands]float64
	peaks   [EQBands]float64
	playing bool
}

var eq = newEQState()

func newEQState() *eqState {
	e := &eqState{}
	for i := range e.peaks {
		e.peaks[i] = 1e-3
	}
	return e
}

// EQLevels returns the current 8 band levels (0-1) and whether audio is
// actively playing. Called from the "eq_levels" NDJSON command so Python's
// LCD loop can poll it while state == "speaking" — see CLAUDE.md's
// "LCD speaking state" note for why this exists (the daemon plays audio
// itself and hands nothing back to Python otherwise).
func EQLevels() ([EQBands]float64, bool) {
	eq.mu.Lock()
	defer eq.mu.Unlock()
	return eq.levels, eq.playing
}

// setEQPlaying marks playback start/stop. Levels are zeroed on stop so a
// stale loud frame doesn't linger on screen after audio actually ends.
func setEQPlaying(playing bool) {
	eq.mu.Lock()
	eq.playing = playing
	if !playing {
		eq.levels = [EQBands]float64{}
	}
	eq.mu.Unlock()
}

// updateEQ computes one Goertzel magnitude per band over `samples` (mono,
// -1..1 float) and folds it into the live level state, each band normalized
// against its own slow-decaying peak (same online-AGC shape as lipsync.py's
// noise/peak tracking) so a quiet passage still shows motion instead of
// flatlining.
func updateEQ(samples []float64, sampleRate int) {
	if len(samples) == 0 {
		return
	}
	var levels [EQBands]float64
	for i, f := range eqFreqs {
		levels[i] = goertzelMag(samples, sampleRate, f)
	}
	eq.mu.Lock()
	for i := range levels {
		eq.peaks[i] = math.Max(levels[i], eq.peaks[i]*0.995)
		v := levels[i] / math.Max(eq.peaks[i], 1e-6)
		if v < 0 {
			v = 0
		} else if v > 1 {
			v = 1
		}
		eq.levels[i] = v
	}
	eq.mu.Unlock()
}

// goertzelMag returns the magnitude of the single-bin DFT at freq Hz —
// cheaper than a full FFT when only a handful of target frequencies matter.
func goertzelMag(samples []float64, sampleRate int, freq float64) float64 {
	n := len(samples)
	k := int(0.5 + float64(n)*freq/float64(sampleRate))
	w := 2 * math.Pi * float64(k) / float64(n)
	cosine := math.Cos(w)
	coeff := 2 * cosine
	var q0, q1, q2 float64
	for _, s := range samples {
		q0 = coeff*q1 - q2 + s
		q2 = q1
		q1 = q0
	}
	real := q1 - q2*cosine
	imag := q2 * math.Sin(w)
	return math.Sqrt(real*real+imag*imag) / float64(n)
}

// pcmToMonoFloat converts S16LE PCM bytes to mono float64 samples in -1..1,
// averaging channels down if interleaved.
func pcmToMonoFloat(pcm []byte, channels int) []float64 {
	if channels < 1 {
		channels = 1
	}
	frameBytes := 2 * channels
	nFrames := len(pcm) / frameBytes
	out := make([]float64, nFrames)
	for f := 0; f < nFrames; f++ {
		var sum float64
		base := f * frameBytes
		for c := 0; c < channels; c++ {
			v := int16(binary.LittleEndian.Uint16(pcm[base+c*2 : base+c*2+2]))
			sum += float64(v) / 32768.0
		}
		out[f] = sum / float64(channels)
	}
	return out
}

// eqChunkFrames is how many audio frames make up one level-update chunk —
// 1/30s, the same granularity zeev.py's _play_pcm_chunked uses.
func eqChunkFrames(sampleRate int) int {
	f := sampleRate / 30
	if f < 1 {
		f = 1
	}
	return f
}

// levelTrackingWriter wraps an io.Writer (aplay's stdin), splitting each
// Write into small audio-time chunks so updateEQ gets fresh data roughly
// every 33ms, then forwards the bytes through unchanged. It relies on the
// OS pipe + aplay's own (tightened, see -B/-F flags below) ALSA buffer to
// provide the pacing — same reasoning zeev.py's _play_pcm_chunked comment
// gives for its explicit sleep-based pacing, but here the natural
// backpressure of small sequential writes to a nearly-full pipe is enough:
// no explicit sleep needed, and one less place to get real-time drift wrong
// in a goroutine.
type levelTrackingWriter struct {
	w        writerFn
	rate     int
	channels int
}

type writerFn func(p []byte) (int, error)

func (lw *levelTrackingWriter) Write(p []byte) (int, error) {
	frameBytes := 2 * lw.channels
	if frameBytes < 2 {
		frameBytes = 2
	}
	chunkBytes := eqChunkFrames(lw.rate) * frameBytes
	if chunkBytes < frameBytes {
		chunkBytes = frameBytes
	}
	total := 0
	for off := 0; off < len(p); off += chunkBytes {
		end := off + chunkBytes
		if end > len(p) {
			end = len(p)
		}
		chunk := p[off:end]
		updateEQ(pcmToMonoFloat(chunk, lw.channels), lw.rate)
		n, err := lw.w(chunk)
		total += n
		if err != nil {
			return total, err
		}
	}
	return total, nil
}
