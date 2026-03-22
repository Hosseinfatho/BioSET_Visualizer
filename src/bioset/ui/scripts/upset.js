Vue.component('upset-plot', {
  props: {
    data: Array,
    dataLocal: Array,
    dataViewport: Array,
    channelData: Array,
    viewMode: String,
    metric: {
      type: String,
      default: 'iou'
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
    viewMode: 'render',
    metric: 'render',
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
      const sourceData = this.viewMode === 'local' ? (this.dataLocal || []) : this.viewMode === 'viewport' ? (this.dataViewport || []) : (this.data || []);
      if (sourceData.length === 0) {
            this.$refs.container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px;">No channel is selected</div>';
            return;
        }

      let maxMetricValue = 0;
      if (sourceData.length > 0) {
        const values = sourceData.map(d => d[this.metric]);
        maxMetricValue = Math.max(...values);
      }

      const start = this.offset;
      const end = start + this.limit;
      const renderData = sourceData.slice(start, end);

      const mappedData = renderData.map(item => ({
        sets: item.channels,
        cardinality: item[this.metric]
      }));

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
        yDomain: [0, maxMetricValue],
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