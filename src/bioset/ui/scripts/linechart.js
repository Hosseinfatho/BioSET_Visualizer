Vue.component('linechart', {
    props: {
        data: Object,
        channelData: Array,
        viewMode: String,
        metric: String,
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
        data: { handler: 'render', deep: true },
        metric: 'render',
        viewMode: 'render',
        channelData: { handler: 'render', deep: true }
    },
    mounted() {
        this.render();
    },
    methods: {
        render() {
            if (!this.$refs.container || !window.d3) return;

            const container = this.$refs.container;
            d3.select(container).selectAll("*").remove();

            if (!this.data || Object.keys(this.data).length === 0) {
                container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px; text-align: center;">Select a channel to see<br>dilation curve</div>';
                return;
            }

            const width = this.width;
            const height = this.height;
            const marginTop = 30;
            const marginRight = 70;
            const marginBottom = 50;
            const marginLeft = 50;

            const svg = d3.select(container)
                .append("svg")
                .attr("width", width)
                .attr("height", height)
                .attr("viewBox", [0, 0, width, height]);

            // Gather all points to compute scales
            let allDilations = [];
            let allValues = [];
            
            const lines = Object.keys(this.data).map(key => {
                const curveData = this.data[key];
                const points = curveData.map(d => ({
                    x: d.dilation !== undefined ? d.dilation : 0,
                    y: d[this.metric] !== undefined ? d[this.metric] : null
                })).filter(d => d.y !== null && !isNaN(d.y));
                
                points.forEach(p => {
                    allDilations.push(p.x);
                    allValues.push(p.y);
                });
                
                return {
                    key: key,
                    points: points,
                    isCombo: key.includes('|')
                };
            });
            
            if (allDilations.length === 0) {
                container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px;">No data available for this metric</div>';
                return;
            }

            const xDomain = d3.extent(allDilations);
            if (xDomain[0] === xDomain[1]) {
                xDomain[0] -= 1;
                xDomain[1] += 1;
            }
            
            const yMax = d3.max(allValues);
            const yMin = d3.min(allValues);
            const padding = (yMax - yMin) * 0.1 || (yMax * 0.1) || 0.1;
            const yDomain = [Math.max(0, yMin - padding), yMax + padding];

            const x = d3.scaleLinear()
                .domain(xDomain)
                .range([marginLeft, width - marginRight]);

            const y = d3.scaleLinear()
                .domain(yDomain)
                .range([height - marginBottom, marginTop]);

            svg.append("g")
                .attr("transform", `translate(0,${height - marginBottom})`)
                .call(d3.axisBottom(x).ticks(5))
                .selectAll("text")
                .attr("fill", "white")
                .style("font-size", "12px");

            svg.append("text")
                .attr("x", marginLeft + (width - marginLeft - marginRight) / 2)
                .attr("y", height - 10)
                .attr("text-anchor", "middle")
                .style("fill", "white")
                .style("font-size", "12px")
                .text("Dilation");

            const metricLabels = {
                "iou": "IoU",
                "overlap_coeff": "Overlap Coeff",
                "density": "Density",
                "count": "Voxel Count"
            };

            svg.append("g")
                .attr("transform", `translate(${marginLeft},0)`)
                .call(d3.axisLeft(y).ticks(5).tickFormat(d => {
                    if(d >= 1000) return (d/1000).toFixed(1) + "k";
                    return d;
                }))
                .call(g => g.select(".domain").remove())
                .selectAll("text")
                .attr("fill", "white")
                .style("font-size", "12px");

            svg.append("text")
                .attr("x", -marginTop - (height - marginTop - marginBottom) / 2)
                .attr("y", 15)
                .attr("transform", "rotate(-90)")
                .attr("text-anchor", "middle")
                .style("fill", "white")
                .style("font-size", "12px")
                .text(metricLabels[this.metric] || this.metric);

            svg.selectAll(".domain").attr("stroke", "white");
            svg.selectAll(".tick line").attr("stroke", "#444");

            const lineGen = d3.line()
                .x(d => x(d.x))
                .y(d => y(d.y));

            lines.forEach(lineObj => {
                let color = "#FFFFFF";
                let strokeWidth = 2;

                let displayLabel = lineObj.key;
                if (lineObj.isCombo) {
                    color = "#FFFFFF";
                    strokeWidth = 3;
                    const channelNames = lineObj.key.split('|');
                    if (channelNames.length > 3) {
                        displayLabel = `[${channelNames.slice(0, 3).join(', ')}, ...]`;
                    } else {
                        displayLabel = `[${channelNames.join(', ')}]`;
                    }
                } else if (this.channelData) {
                    const ch = this.channelData.find(c => c.name === lineObj.key);
                    if (ch) {
                        color = ch.color;
                    }
                }

                svg.append("path")
                    .datum(lineObj.points)
                    .attr("fill", "none")
                    .attr("stroke", color)
                    .attr("stroke-width", strokeWidth)
                    .attr("d", lineGen);

                if (lineObj.points.length > 0) {
                    const lastPoint = lineObj.points[lineObj.points.length - 1];
                    svg.append("text")
                        .attr("x", x(lastPoint.x) + 5)
                        .attr("y", y(lastPoint.y))
                        .attr("alignment-baseline", "middle")
                        .attr("fill", color)
                        .style("font-size", "10px")
                        .style("font-weight", lineObj.isCombo ? "bold" : "normal")
                        .text(displayLabel);
                }

                svg.selectAll(`.point-${lineObj.key.replace(/[^a-zA-Z0-9]/g, "_")}`)
                    .data(lineObj.points)
                    .enter()
                    .append("circle")
                    .attr("class", `point-${lineObj.key.replace(/[^a-zA-Z0-9]/g, "_")}`)
                    .attr("cx", d => x(d.x))
                    .attr("cy", d => y(d.y))
                    .attr("r", 4)
                    .attr("fill", color)
                    .append("title")
                    .text(d => `${displayLabel}: ${d.y.toFixed(4)} at dilation ${d.x}`);
            });
        }
    }
});
