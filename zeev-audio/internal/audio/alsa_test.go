package audio

import "testing"

// Must match CURVE in tests/test_volume_curve.py exactly. The Python fallback
// runs whenever the daemon is down, so two different curves would mean the same
// percentage produced audibly different volumes depending on which path set it.
func TestPctToRaw(t *testing.T) {
	cases := []struct{ pct, raw int }{
		{0, 0}, {1, 81}, {10, 85}, {25, 92}, {40, 99},
		{50, 104}, {70, 113}, {80, 118}, {90, 122}, {100, 127},
	}
	for _, c := range cases {
		if got := pctToRaw(c.pct); got != c.raw {
			t.Errorf("pctToRaw(%d) = %d, want %d", c.pct, got, c.raw)
		}
	}
}

// Below raw ~48 the WM8960 is under -73 dB and simply off. No audible
// percentage may land there -- that dead zone is the bug being fixed.
func TestNoPercentLandsInTheDeadZone(t *testing.T) {
	for pct := 1; pct <= 100; pct++ {
		if got := pctToRaw(pct); got < 48 {
			t.Errorf("pctToRaw(%d) = %d, inside the inaudible zone", pct, got)
		}
	}
}

func TestClampAndMute(t *testing.T) {
	if got := pctToRaw(0); got != 0 {
		t.Errorf("0%% must be true mute, got %d", got)
	}
	if got := pctToRaw(-5); got != 0 {
		t.Errorf("negative must clamp to mute, got %d", got)
	}
	if got := pctToRaw(500); got != 127 {
		t.Errorf("over 100 must clamp to 127, got %d", got)
	}
}

func TestMonotonic(t *testing.T) {
	prev := -1
	for pct := 0; pct <= 100; pct++ {
		got := pctToRaw(pct)
		if got < prev {
			t.Fatalf("not monotonic at %d%%: %d after %d", pct, got, prev)
		}
		prev = got
	}
}
