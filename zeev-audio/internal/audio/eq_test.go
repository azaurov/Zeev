package audio

import (
	"math"
	"testing"
)

func sineSamples(freq float64, sampleRate, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = math.Sin(2 * math.Pi * freq * float64(i) / float64(sampleRate))
	}
	return out
}

// Goertzel at (near) the tone's own frequency must read far higher than at a
// frequency an octave-plus away — this is the whole basis for the equalizer
// bars actually looking like they're tracking real pitch content rather than
// just overall loudness.
func TestGoertzelMagPicksOutItsOwnFrequency(t *testing.T) {
	const rate = 24000
	samples := sineSamples(1000, rate, rate/30)
	near := goertzelMag(samples, rate, 1000)
	far := goertzelMag(samples, rate, 250)
	if near <= far*3 {
		t.Fatalf("goertzelMag(1000Hz tone, @1000Hz)=%.4f not clearly above @250Hz=%.4f", near, far)
	}
}

func TestGoertzelMagSilenceIsNearZero(t *testing.T) {
	samples := make([]float64, 800)
	if got := goertzelMag(samples, 24000, 1000); got > 1e-9 {
		t.Fatalf("silence should read ~0, got %v", got)
	}
}

func TestUpdateEQPlayingLifecycle(t *testing.T) {
	setEQPlaying(true)
	updateEQ(sineSamples(500, 24000, 800), 24000)
	levels, playing := EQLevels()
	if !playing {
		t.Fatal("expected playing=true")
	}
	sawNonZero := false
	for _, v := range levels {
		if v < 0 || v > 1 {
			t.Fatalf("level out of [0,1] range: %v", v)
		}
		if v > 0 {
			sawNonZero = true
		}
	}
	if !sawNonZero {
		t.Fatal("expected at least one band to register the tone")
	}

	setEQPlaying(false)
	levels, playing = EQLevels()
	if playing {
		t.Fatal("expected playing=false after setEQPlaying(false)")
	}
	for i, v := range levels {
		if v != 0 {
			t.Fatalf("band %d not cleared on stop: %v", i, v)
		}
	}
}

func TestPCMToMonoFloatAveragesChannels(t *testing.T) {
	// One stereo frame: left=+32767 (~1.0), right=-32768 (~-1.0) -> mono ~0.
	pcm := []byte{0xFF, 0x7F, 0x00, 0x80}
	out := pcmToMonoFloat(pcm, 2)
	if len(out) != 1 {
		t.Fatalf("expected 1 frame, got %d", len(out))
	}
	if math.Abs(out[0]) > 0.01 {
		t.Fatalf("expected ~0 from opposite-sign channels, got %v", out[0])
	}
}

func TestLevelTrackingWriterForwardsAllBytesUnchanged(t *testing.T) {
	var got []byte
	lw := &levelTrackingWriter{
		w: func(p []byte) (int, error) {
			got = append(got, p...)
			return len(p), nil
		},
		rate:     24000,
		channels: 1,
	}
	// A few chunks' worth of frames so the internal splitting loop runs
	// more than once (eqChunkFrames(24000) = 800 frames = 1600 bytes/chunk).
	pcm := make([]byte, 1600*3+123)
	for i := range pcm {
		pcm[i] = byte(i)
	}
	n, err := lw.Write(pcm)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if n != len(pcm) {
		t.Fatalf("wrote %d bytes, want %d", n, len(pcm))
	}
	if len(got) != len(pcm) {
		t.Fatalf("forwarded %d bytes, want %d", len(got), len(pcm))
	}
	for i := range pcm {
		if got[i] != pcm[i] {
			t.Fatalf("byte %d mismatched: forwarded data was altered", i)
		}
	}
}
