
// Bar Plot Component
Vue.component('bar-plot', {
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
            if (!this.$refs.container || !window.d3) return;

            const container = this.$refs.container;
            d3.select(container).selectAll("*").remove();

            // Determine data
            const sourceData = this.viewMode === 'local' ? (this.dataLocal || []) : (this.data || []);
            // Limit items
            const renderData = sourceData.slice(0, 10);

            if (renderData.length === 0) {
                container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px;">No channel data available</div>';
                return;
            }

            // Constants
            const width = 330;
            const height = 280;
            const marginTop = 30;
            const marginRight = 10;
            const marginBottom = 120;
            const marginLeft = 80;

            const chartData = renderData.map(d => ({ name: d[0], count: d[1] }));

            // Scales
            const x = d3.scaleBand()
                .domain(chartData.map(d => d.name))
                .range([marginLeft, width - marginRight])
                .padding(0.1);

            const y = d3.scaleLinear()
                .domain([0, d3.max(chartData, d => d.count)])
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
                .attr("y", d => y(d.count))
                .attr("height", d => y(0) - y(d.count))
                .attr("width", x.bandwidth())
                .attr("fill", "#FFFFFF")
                .attr("cursor", "pointer")
                .on("click", (event, d) => {
                    this.$emit('click', {
                        name: d.name,
                        count: d.count,
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
                .call(d3.axisLeft(y).tickFormat(d3.format(".2e")))
                .call(g => g.select(".domain").remove())
                .selectAll("text")
                .attr("fill", "white")
                .style("font-size", "12px");
        }
    }
});
