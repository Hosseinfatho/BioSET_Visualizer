// Axis helpers, guarded so whichever chart script loads first defines them.
if (typeof bsNiceLogTicks === 'undefined') {
    window.bsNiceLogTicks = function (lo, hi, maxTicks) {
        if (!(lo > 0) || !(hi > lo)) return [];
        const n = Math.max(2, maxTicks || 5);
        const e0 = Math.ceil(Math.log10(lo));
        const e1 = Math.floor(Math.log10(hi));
        const decades = [];
        for (let e = e0; e <= e1; e++) decades.push(Math.pow(10, e));
        if (decades.length >= 3 && decades.length <= n) return decades;
        const l0 = Math.log10(lo), l1 = Math.log10(hi);
        const out = [];
        for (let i = 0; i < n; i++) {
            const v = Math.pow(10, l0 + ((l1 - l0) * i) / (n - 1));
            const mag = Math.pow(10, Math.floor(Math.log10(v)) - 1);
            const r = Math.round(v / mag) * mag;
            if (r > 0 && isFinite(r)) out.push(r);
        }
        return out.filter((v, i, a) => i === 0 || v !== a[i - 1]);
    };
}
if (typeof bsCompactNum === 'undefined') {
    window.bsCompactNum = function (v) {
        const a = Math.abs(v);
        if (!isFinite(v)) return "";
        if (a >= 1e9) return (v / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "G";
        if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
        if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
        if (a >= 100) return v.toFixed(0);
        if (a >= 10) return v.toFixed(a % 1 ? 1 : 0);
        if (a >= 1) return v.toFixed(1);
        if (a >= 0.01) return v.toFixed(2);
        return v.toExponential(0);
    };
}


// Bar Plot Component
Vue.component('bar-plot', {
    props: {
        data: Array,
        dataLocal: Array,
        dataViewport: Array,
        dataViewportSelected: Array,
        channelData: Array,
        scopeMode: String,
        channelMode: String,
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
            default: 10
        },
        width: {
            type: Number,
            default: 330
        },
        height: {
            type: Number,
            default: 280
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
            if (!this.$refs.container || !window.d3) return;

            const container = this.$refs.container;
            d3.select(container).selectAll("*").remove();
            const sourceData = this.scopeMode === 'local'
                ? (this.channelMode === 'selected' ? (this.dataViewportSelected || []) : (this.dataViewport || []))
                : (this.channelMode === 'selected' ? (this.dataLocal || []) : (this.data || []));

            const maxCount = sourceData.length > 0 ? d3.max(sourceData, d => d[1]) : 0;

            const start = this.offset;
            const end = start + this.limit;
            const renderData = sourceData.slice(start, end);

            if (renderData.length === 0) {
                container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px;">No channel is selected</div>';
                return;
            }

            // Constants
            const width = this.width;
            const height = this.height;
            const marginTop = 30;
            const marginRight = 20;
            const marginBottom = 80;
            const marginLeft = 50;

            const chartData = renderData.map(d => ({ name: d[0], pct: d[1] }));

            // Scales
            const x = d3.scaleBand()
                .domain(chartData.map(d => d.name))
                .range([marginLeft, width - marginRight])
                .padding(0.1);

            // Y scale. Coverage can legitimately be 0, and log(0) is undefined,
            // so the domain floors at the smallest positive value on the page
            // (or a decade below the max when everything is zero). The floor
            // doubles as the bar baseline below — `y(0)` would be -Infinity.
            const positives = sourceData.map(d => d[1]).filter(v => v > 0 && isFinite(v));
            const minPositive = positives.length ? Math.min(...positives) : 0;
            const useLog = this.scaleMode === 'log' && minPositive > 0 && maxCount > 0;
            const yFloor = useLog ? minPositive / 2 : 0;
            const yTop = maxCount > yFloor ? maxCount : (yFloor || 1);

            const y = (useLog ? d3.scaleLog() : d3.scaleLinear())
                .domain([yFloor || (useLog ? 1e-6 : 0), yTop])
                .range([height - marginBottom, marginTop])
                .clamp(true);
            const yBase = y.range()[0];

            const svg = d3.select(container)
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .attr("viewBox", [0, 0, width, height]);

            // Bars
            svg.append("g")
                .selectAll("rect")
                .data(chartData)
                .join("rect")
                .attr("x", d => x(d.name))
                .attr("y", d => (d.pct > 0 ? y(d.pct) : yBase))
                .attr("height", d => (d.pct > 0 ? Math.max(0, yBase - y(d.pct)) : 0))
                .attr("width", x.bandwidth())
                .attr("fill", d => {
                    if (this.channelData) {
                        const channel = this.channelData.find(c => c.name === d.name);
                        if (channel) {
                            return channel.color
                        };
                    }
                    return "#FFFFFF";
                })
                .attr("cursor", "pointer")
                .on("click", (event, d) => {
                    this.$emit('click', {
                        name: d.name,
                        pct: d.pct,
                        ts: Date.now()
                    });
                });

            // X-axis
            svg.append("g")
                .attr("transform", `translate(0,${height - marginBottom})`)
                .call(d3.axisBottom(x).tickSizeOuter(0))
                .selectAll("text")
                .attr("fill", "white")
                .style("text-anchor", "end")
                .attr("dx", "-.8em")
                .attr("dy", ".15em")
                .attr("transform", "rotate(-65)")
                .style("font-size", "12px");

            svg.select(".domain").attr("stroke", "white");
            svg.selectAll(".tick line").attr("stroke", "white");

            // Y-axis
            svg.append("g")
                .attr("transform", `translate(${marginLeft},0)`)
                // Explicit tick values: d3's log scale emits every 1..9 multiple
                // per decade when the span is short (10+ labels here), and a
                // custom tickFormat discards its own label thinning.
                .call(
                    d3.axisLeft(y)
                        .tickValues(
                            useLog
                                ? bsNiceLogTicks(y.domain()[0], y.domain()[1], 5)
                                : y.ticks(5)
                        )
                        .tickFormat(d => bsCompactNum(d) + "%")
                )
                .call(g => g.select(".domain").remove())
                .selectAll("text")
                .attr("fill", "white")
                .style("font-size", "12px");
        }
    }
});
