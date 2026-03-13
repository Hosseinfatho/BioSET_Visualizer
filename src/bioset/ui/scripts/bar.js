
// Bar Plot Component
Vue.component('bar-plot', {
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
            if (!this.$refs.container || !window.d3) return;

            const container = this.$refs.container;
            d3.select(container).selectAll("*").remove();
            const sourceData = this.viewMode === 'local' ? (this.dataLocal || []) : (this.data || []);

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

            const y = d3.scaleLinear()
                .domain([0, maxCount])
                .range([height - marginBottom, marginTop]);

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
                .attr("y", d => y(d.pct))
                .attr("height", d => y(0) - y(d.pct))
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
                .call(d3.axisLeft(y).ticks(5).tickFormat(d => d + "%"))
                .call(g => g.select(".domain").remove())
                .selectAll("text")
                .attr("fill", "white")
                .style("font-size", "12px");
        }
    }
});
