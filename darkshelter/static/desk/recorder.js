// recorder.js: ScriptProcessor recorder with endpointing (SPEC §10). No AudioWorklet in Stage I.
// - noise floor = running minimum of 100 ms frame RMS
// - speech = RMS > max(3 x floor, -45 dBFS)
// - auto-stop 2.5 s after the last speech frame (only once speech was heard)
// - no speech within 8 s -> stop (server answers 「沒有聽到聲音」)
// - a second click stops; hard stop at 30 s
// On stop the audio is encoded as 16-bit mono WAV at the native rate (the server resamples).
(function () {
  "use strict";
  var SPEECH_FLOOR = Math.pow(10, -45 / 20);

  function encodeWav(chunks, sampleRate) {
    var n = 0, i, j;
    for (i = 0; i < chunks.length; i++) n += chunks[i].length;
    var buf = new ArrayBuffer(44 + n * 2);
    var v = new DataView(buf);
    function str(off, s) { for (var k = 0; k < s.length; k++) v.setUint8(off + k, s.charCodeAt(k)); }
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, "data"); v.setUint32(40, n * 2, true);
    var off = 44;
    for (i = 0; i < chunks.length; i++) {
      var c = chunks[i];
      for (j = 0; j < c.length; j++) {
        var s = Math.max(-1, Math.min(1, c[j]));
        v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        off += 2;
      }
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  function Recorder(opts) {
    this.onLevel = opts.onLevel || function () {};
    this.onStop = opts.onStop || function () {};
    this.onError = opts.onError || function () {};
    this.state = "idle";
    this.ctx = null;
  }

  // Must be called from the click handler (creates/resumes the AudioContext there).
  Recorder.prototype.start = function () {
    var self = this;
    if (self.state !== "idle") return;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      self.onError(new Error("no_audio_api"));
      return;
    }
    if (!self.ctx) self.ctx = new AC();
    if (self.ctx.state === "suspended") self.ctx.resume();
    self.state = "starting";
    navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    }).then(function (stream) {
      if (self.state !== "starting") { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
      self.stream = stream;
      self.source = self.ctx.createMediaStreamSource(stream);
      self.proc = self.ctx.createScriptProcessor(4096, 1, 1);
      self.sampleRate = self.ctx.sampleRate;
      self.chunks = [];
      self.frameLen = Math.max(1, Math.round(self.sampleRate * 0.1));
      self.frame = new Float32Array(self.frameLen);
      self.fill = 0;
      self.floor = Infinity;
      self.heard = false;
      self.lastSpeech = 0;
      self.samples = 0;
      self.proc.onaudioprocess = function (e) { self._onAudio(e); };
      self.source.connect(self.proc);
      self.proc.connect(self.ctx.destination);
      self.state = "recording";
      self.startT = performance.now();
      self.hardStop = setTimeout(function () { self.stop("max"); }, 30500);
    }).catch(function (err) {
      self.state = "idle";
      self.onError(err);
    });
  };

  Recorder.prototype._onAudio = function (e) {
    if (this.state !== "recording") return;
    var x = e.inputBuffer.getChannelData(0);
    this.chunks.push(new Float32Array(x));
    this.samples += x.length;
    for (var i = 0; i < x.length; i++) {
      this.frame[this.fill++] = x[i];
      if (this.fill === this.frameLen) {
        this.fill = 0;
        if (this._onFrame()) return;
      }
    }
  };

  Recorder.prototype._onFrame = function () {
    var s = 0, f = this.frame;
    for (var i = 0; i < f.length; i++) s += f[i] * f[i];
    var rms = Math.sqrt(s / f.length);
    if (rms < this.floor) this.floor = rms;
    var speech = rms > Math.max(3 * this.floor, SPEECH_FLOOR);
    var t = this.samples / this.sampleRate;
    if (speech) { this.heard = true; this.lastSpeech = t; }
    this.onLevel(20 * Math.log10(rms + 1e-9), speech, t);
    if (t >= 30) { this.stop("max"); return true; }
    if (this.heard && t - this.lastSpeech >= 2.5) { this.stop("silence"); return true; }
    if (!this.heard && t >= 8) { this.stop("no_speech"); return true; }
    return false;
  };

  Recorder.prototype.stop = function (reason) {
    var self = this;
    if (self.state === "starting") { self.state = "idle"; return; }
    if (self.state !== "recording") return;
    self.state = "stopping";
    clearTimeout(self.hardStop);
    try { self.proc.disconnect(); } catch (e) { /* ignore */ }
    try { self.source.disconnect(); } catch (e) { /* ignore */ }
    try { self.stream.getTracks().forEach(function (tr) { tr.stop(); }); } catch (e) { /* ignore */ }
    var seconds = self.samples / self.sampleRate;
    var wav = encodeWav(self.chunks, self.sampleRate);
    self.chunks = [];
    self.state = "idle";
    self.onStop(wav, reason || "click", seconds);
  };

  Recorder.prototype.isRecording = function () { return this.state === "recording" || this.state === "starting"; };

  window.DSRecorder = Recorder;
  window.DSencodeWav = encodeWav;
})();
