Vue.component("channel-histogram", {
  props: {
    bins: { type: Array, default: () => [] },
    color: { type: String, default: "#FFFFFF" },
    height: { type: Number, default: 30 },
  },
  template: `
    <div
      :style="{
        display: 'flex',
        alignItems: 'flex-end',
        height: height + 'px',
        width: '100%',
        gap: '1px',
      }"
    >
      <div
        v-for="(val, i) in bins"
        :key="i"
        :style="{
          flex: 1,
          height: (val * height) + 'px',
          backgroundColor: color,
          borderRadius: '1px 1px 0 0',
          minWidth: '2px',
          opacity: 0.7,
        }"
      ></div>
    </div>
  `,
});
