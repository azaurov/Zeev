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

func TestParseResolve(t *testing.T) {
	const u = "https://rr3---sn-x.googlevideo.com/videoplayback?expire=1"
	title, url, err := parseResolve("Ray Parker Jr. - Ghostbusters\n"+u+"\n", "ghost")
	if err != nil || title != "Ray Parker Jr. - Ghostbusters" || url != u {
		t.Fatalf("got %q %q %v", title, url, err)
	}
	// A title that itself looks like a URL must not be taken for the stream.
	_, url, _ = parseResolve("https://not-the-stream.example\n"+u, "q")
	if url != u {
		t.Errorf("stream URL must be the last http line, got %q", url)
	}
	// Missing title falls back to the query.
	if title, _, _ := parseResolve(u, "ghost"); title != "ghost" {
		t.Errorf("title fallback = %q", title)
	}
	// No URL is an error, never a silent "playing" of nothing.
	if _, _, err := parseResolve("Some Title\n", "q"); err == nil {
		t.Error("expected an error when yt-dlp printed no URL")
	}
	if _, _, err := parseResolve("", "q"); err == nil {
		t.Error("expected an error on empty output")
	}
}
