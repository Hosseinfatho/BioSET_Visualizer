
// UpSet Plot Component
Vue.component('upset-plot', {
  props: ['data', 'dataLocal', 'viewMode'],
  template: '<div ref="container"></div>',
  watch: {
    data: 'render',
    dataLocal: 'render',
    viewMode: 'render'
  },
  mounted() {
    this.render();
  },
  methods: {
    render() {
      if (!this.$refs.container || !window.UpSetJS) return;

      // Determine which data to use
      const sourceData = this.viewMode === 'local' ? (this.dataLocal || []) : (this.data || []);
      // Safety slice
      const renderData = sourceData.slice(0, 7);

      // Transform data as expected by UpSetJS
      const mappedData = renderData.map(item => ({
        sets: item.channels,
        cardinality: item.count
      }));

      const { sets, combinations } = UpSetJS.extractFromExpression(mappedData);

      // Render
      this.$refs.container.innerHTML = "";
      UpSetJS.render(this.$refs.container, {
        sets,
        combinations,
        width: 330,
        height: 340,
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
        heightRatios: [0.5],
        exportButtons: false,
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