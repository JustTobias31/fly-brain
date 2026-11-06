// Explicit dopamine signalling model for the fly simulation.
// Receptor occupancy is a phenomenological state, not a receptor-neuron population in the connectome.
// PAM/PPL1 activity is read from the connectome where those cell classes are present.
const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));
const smooth = (x, half) => x / (half + x);
export class DopamineSystem {
  constructor(data, brain) {
    this.brain = brain;
    this.pam = []; this.ppl1 = [];
    for (let i = 0; i < data.N; i++) {
      const type = String(data.meta.types[i] || '');
      if (/^PAM(?:[-_ ]|$)/i.test(type)) this.pam.push(i);
      if (/^PPL1(?:[-_ ]|$)/i.test(type)) this.ppl1.push(i);
    }
    this.lastPam = Uint32Array.from(this.pam, i => brain.spikeCount[i]);
    this.lastPpl1 = Uint32Array.from(this.ppl1, i => brain.spikeCount[i]);
    this.pamHz = 0; this.ppl1Hz = 0; this.level = 0; this.positive = 0; this.aversive = 0;
    this.receptors = { Dop1R1: 0, Dop1R2: 0, Dop2R: 0 };
  }
  pulse(value = 1) {
    const v = clamp(Math.abs(Number(value) || 0), 0, 5) / 3;
    if (value >= 0) this.positive = clamp(this.positive + v);
    else this.aversive = clamp(this.aversive + v);
    this.level = clamp(this.level + (value >= 0 ? 1 : -1) * v * 0.75);
    return this.readout();
  }
  update(dtMs = 1) {
    const rate = (ids, last) => {
      if (!ids.length) return 0;
      let spikes = 0;
      for (let n = 0; n < ids.length; n++) { const now = this.brain.spikeCount[ids[n]]; spikes += now - last[n]; last[n] = now; }
      return spikes * 1000 / (dtMs * ids.length);
    };
    const pam = rate(this.pam, this.lastPam), ppl1 = rate(this.ppl1, this.lastPpl1), k = 1 - Math.exp(-dtMs / 80);
    this.pamHz += (pam - this.pamHz) * k; this.ppl1Hz += (ppl1 - this.ppl1Hz) * k;
    const endogenous = clamp((this.pamHz + this.ppl1Hz) / 80);
    const target = clamp(endogenous + this.positive - this.aversive);
    this.level += (target - this.level) * (1 - Math.exp(-dtMs / 180));
    this.positive *= Math.exp(-dtMs / 450); this.aversive *= Math.exp(-dtMs / 450);
    const da = this.level;
    this.receptors.Dop1R1 += (smooth(da, 0.18) - this.receptors.Dop1R1) * (1 - Math.exp(-dtMs / 120));
    this.receptors.Dop1R2 += (smooth(da, 0.32) - this.receptors.Dop1R2) * (1 - Math.exp(-dtMs / 180));
    this.receptors.Dop2R += (smooth(da, 0.12) - this.receptors.Dop2R) * (1 - Math.exp(-dtMs / 90));
  }
  readout() {
    return { level: this.level, positive: this.positive, aversive: this.aversive, receptors: { ...this.receptors }, pamHz: this.pamHz, ppl1Hz: this.ppl1Hz, pamNeurons: this.pam.length, ppl1Neurons: this.ppl1.length, receptorModel: 'phenomenological-occupancy' };
  }
}
