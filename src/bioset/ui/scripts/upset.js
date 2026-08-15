// Axis scale for the combination bars.
//
// UpSetJS's own `numericScale: 'log'` cannot be used here. Its implementation
// (v17.0.2, transcribed from the bundle) is:
//
//   var a = max < 1 ? 1 : Math.log10(max);
//   position = value <= 1 ? 0 : Math.log10(value) / a;
//
// Every value <= 1 maps to position 0 — and IoU and overlap coefficient are
// both in [0, 1], so switching it on would blank the entire chart. The prop
// validator accepts a function, so we supply our own; it must expose `ticks`
// and `tickFormat` the way the built-ins do.
//
// Both modes span [min, max] of the visible page rather than starting at zero,
// because a page is the top N of a sorted list and its values are necessarily
// close together.
function makeScale(mode, lo, hi) {
  return function (max, range, options) {
    const y0 = range[0];
    const y1 = range[1];
    const span = y1 - y0;
    // Leave a little headroom so the smallest bar is visible rather than empty.
    let dLo = lo > 0 ? lo : (hi > 0 ? hi / 1000 : 0);
    let dHi = hi > dLo ? hi : dLo * 10;
    const useLog = mode === 'log' && dLo > 0;
    const lLo = useLog ? Math.log10(dLo) : dLo;
    const lHi = useLog ? Math.log10(dHi) : dHi;
    const pad = (lHi - lLo) * 0.08 || 1;
    const aLo = lLo - pad;

    const scale = function (v) {
      if (!isFinite(v) || v <= 0) return y0;
      const t = useLog ? Math.log10(v) : v;
      const frac = (t - aLo) / (lHi - aLo);
      return y0 + Math.max(0, Math.min(1, frac)) * span;
    };
    const fmt = function (v) {
      const a = Math.abs(v);
      if (!isFinite(v)) return "";
      if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
      if (a >= 1e3) return (v / 1e3).toFixed(1) + "k";
      if (a >= 10) return v.toFixed(0);
      if (a >= 1) return v.toFixed(1);
      if (a >= 0.01) return v.toFixed(2);
      return v.toExponential(0);
    };
    scale.ticks = function (count) {
      // Hard cap: UpSetJS asks for as many ticks as it thinks fit, which on a
      // ~140px bar axis is more labels than there is room for once they carry
      // 4 characters each.
      const n = Math.min(4, Math.max(2, count || 4));
      const out = [];
      for (let i = 0; i < n; i++) {
        const t = aLo + ((lHi - aLo) * i) / (n - 1);
        const v = useLog ? Math.pow(10, t) : t;
        if (v > 0 && isFinite(v)) out.push({ value: v, label: fmt(v) });
      }
      return out;
    };
    scale.tickFormat = function () { return fmt; };
    return scale;
  };
}

Vue.component('upset-plot', {
  props: {
    data: Array,
    dataLocal: Array,
    dataViewport: Array,
    dataViewportSelected: Array,
    channelData: Array,
    scopeMode: String,
    channelMode: String,
    metric: {
      type: String,
      default: 'iou'
    },
    scaleMode: {
      type: String,
      default: 'log'
    },
    offset: {
      type: Number,
      default: 0
    },
    limit: {
      type: Number,
      default: 7
    },
    width: {
      type: Number,
      default: 330
    },
    height: {
      type: Number,
      default: 340
    }
  },
  template: '<div class="plot" ref="container"></div>',
  watch: {
    data: 'render',
    dataLocal: 'render',
    dataViewport: 'render',
    dataViewportSelected: 'render',
    scopeMode: 'render',
    channelMode: 'render',
    metric: 'render',
    scaleMode: 'render',
    offset: 'render',
    limit: 'render',
    channelData: {
      handler: 'render',
      deep: true
    }
  },
  mounted() {
    this.render();
  },
  methods: {
    render() {
      if (!this.$refs.container || !window.UpSetJS) return;
      const sourceData = this.scopeMode === 'local'
        ? (this.channelMode === 'selected' ? (this.dataViewportSelected || []) : (this.dataViewport || []))
        : (this.channelMode === 'selected' ? (this.dataLocal || []) : (this.data || []));
      if (sourceData.length === 0) {
            // Distinguish the two ways of being empty: with the filter fixed,
            // "nothing matched" is a real answer and not the same as "you have
            // not picked any channels".
            const why = (this.channelMode === 'selected' || this.scopeMode === 'local')
              ? 'No combinations match the current filter'
              : 'No channel is selected';
            this.$refs.container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px; text-align: center; padding: 0 12px;">' + why + '</div>';
            return;
        }

      const metric = this.metric;
      const sorted = sourceData.slice().sort((a, b) => (b[metric] || 0) - (a[metric] || 0));

      const start = this.offset;
      const end = start + this.limit;
      const renderData = sorted.slice(start, end);

      const mappedData = renderData.map(item => ({
        sets: item.channels,
        cardinality: item[this.metric]
      }));

      // Axis domain over the VISIBLE page, not [0, max].
      //
      // These bars are ratios (IoU / overlap coefficient), and a page is the
      // top N of a sorted list, so their values sit in a narrow band: the
      // default top-7 spans 0.71..0.56, which against a zero-anchored axis is
      // 100% vs 78% of full length and reads as "every bar is the same".
      // Anchoring the domain at the page minimum is what separates them.
      const pageValues = mappedData.map(d => d.cardinality).filter(v => v > 0 && isFinite(v));
      const pageMax = pageValues.length ? Math.max(...pageValues) : 1;
      const pageMin = pageValues.length ? Math.min(...pageValues) : 0;

      const { sets, combinations } = UpSetJS.extractFromExpression(mappedData);

      // sort channels by the number of combinations they appear in
      const combCountBySet = new Map(sets.map(s => [s.name, 0]));
      for (const comb of combinations) {
        const members = comb.sets ? Array.from(comb.sets).map(s => s.name) : [];
        for (const name of members) {
          combCountBySet.set(name, (combCountBySet.get(name) || 0) + 1);
        }
      }
      sets.sort((a, b) => {
        const da = combCountBySet.get(a.name) || 0;
        const db = combCountBySet.get(b.name) || 0;
        return db-da || a.name.localeCompare(b.name); 
      });

      if (this.channelData && this.channelData.length > 0) {
        sets.forEach(set => {
          const channel = this.channelData.find(c => c.name === set.name);
          if (!channel) {
            return;
          }
          set.color = channel.color;
        });
      }

      // Render
      this.$refs.container.innerHTML = "";
      UpSetJS.render(this.$refs.container, {
        sets,
        combinations,
        width: this.width,
        height: this.height,
        theme: 'dark',
        color: '#FFFFFF',
        textColor: '#FFFFFF',
        selectionColor: '#FFFFFF',
        notMemberColor: '#4A4A4A',
        fontSizes: {
          axisTick: '12px',
          setLabel: '12px',
          setSize: '0px',
          intersectionLabel: '12px',
          barLabel: '0px',
          chartLabel: '0px',
        },
        widthRatios: [0, 0.35],
        heightRatios: [0.4],
        exportButtons: false,
        // `yDomain` was passed here before and is NOT an UpSetJS prop (it does
        // not appear anywhere in the bundle) — it was silently ignored.
        numericScale: makeScale(this.scaleMode, pageMin, pageMax),
        onClick: (clickedItem) => {
          setTimeout(() => {
            if (!clickedItem) {
              this.$emit('click', null);
              return;
            }

            const setNames = clickedItem.sets
              ? Array.from(clickedItem.sets).map(s => s.name)
              : [clickedItem.name];

            this.$emit('click', {
              name: clickedItem.name,
              sets: setNames,
              size: clickedItem.cardinality,
              ts: Date.now()
            });
          }, 0);
        }
      });
    }
  }
});