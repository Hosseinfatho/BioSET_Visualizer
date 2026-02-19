
// UpSet Plot Component
Vue.component('upset-plot', {
  props: {
    data: Array,
    dataLocal: Array,
    channelData: Array,
    viewMode: String,
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
    viewMode: 'render',
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
      const sourceData = this.viewMode === 'local' ? (this.dataLocal || []) : (this.data || []);

      const maxIou = sourceData.length > 0 ? Math.max(...sourceData.map(d => d.iou)) : 0;

      const start = this.offset;
      const end = start + this.limit;
      const renderData = sourceData.slice(start, end);

      const mappedData = renderData.map(item => ({
        sets: item.channels,
        cardinality: item.iou
      }));

      const { sets, combinations } = UpSetJS.extractFromExpression(mappedData);

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
          setSize: '12px',
          intersectionLabel: '12px',
          barLabel: '0px',
          chartLabel: '0px',
        },
        widthRatios: [0.18, 0.35],
        heightRatios: [0.4],
        exportButtons: false,
        yDomain: [0, maxIou],
        onClick: (clickedItem) => {
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
        }
      });
    }
  }
});