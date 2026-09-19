package music

import "testing"

// A stream that drops mid-song ends ffmpeg cleanly and the track just stops
// (found live 2026-09-19, Ghostbusters theme). The reconnect flags must be
// present and must precede -i, since ffmpeg applies them to the next input.
func TestFFmpegArgsReconnectBeforeInput(t *testing.T) {
	args := ffmpegArgs("https://example.invalid/a")
	idx := map[string]int{}
	for i, a := range args {
		idx[a] = i
	}
	in, ok := idx["-i"]
	if !ok {
		t.Fatal("no -i in args")
	}
	for _, f := range []string{"-reconnect", "-reconnect_streamed", "-reconnect_delay_max"} {
		i, ok := idx[f]
		if !ok {
			t.Fatalf("missing %s", f)
		}
		if i > in {
			t.Errorf("%s must come before -i to apply to the input", f)
		}
	}
	if args[in+1] != "https://example.invalid/a" {
		t.Errorf("input URL not after -i: %v", args)
	}
	if args[len(args)-1] != "pipe:1" {
		t.Errorf("output must remain pipe:1")
	}
}
