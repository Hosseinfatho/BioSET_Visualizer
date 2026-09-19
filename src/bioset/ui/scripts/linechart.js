// Shared axis helpers for the analysis charts.
//
// d3's log scale does not honour a tick *count* the way linear does: for a
// domain spanning less than `count` decades it emits every 1..9 multiple inside
// each decade, which on the dilation curve is 21-24 ticks in ~250px. Worse,
// passing a custom `tickFormat` throws away d3's own label thinning (its
// built-in formatter returns "" for ticks it wants unlabelled), so every one of
// them got drawn. Take explicit control of the values instead.
function bsNiceLogTicks(lo, hi, maxTicks) {
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
        // round to 2 significant figures so labels stay short
        const mag = Math.pow(10, Math.floor(Math.log10(v)) - 1);
        const r = Math.round(v / mag) * mag;
        if (r > 0 && isFinite(r)) out.push(r);
    }
    return out.filter((v, i, a) => i === 0 || v !== a[i - 1]);
}

function bsCompactNum(v) {
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
}

// Elbow of a saturating curve: the point furthest from the straight line
// joining its two endpoints, measured after normalising both axes — the
// standard "knee" construction. It is computed in the space the axis actually
// draws (log or linear) so the marker lands where the bend is visible.
function bsFindElbow(points, useLog) {
    const pts = points.filter(p => isFinite(p.x) && isFinite(p.y) && (!useLog || p.y > 0));
    if (pts.length < 4) return null;
    const xs = pts.map(p => p.x);
    const ys = pts.map(p => (useLog ? Math.log10(p.y) : p.y));
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const xr = (x1 - x0) || 1, yr = (y1 - y0) || 1;
    const xn = xs.map(v => (v - x0) / xr);
    const yn = ys.map(v => (v - y0) / yr);
    const dx = xn[xn.length - 1] - xn[0];
    const dy = yn[yn.length - 1] - yn[0];
    const den = Math.hypot(dx, dy) || 1;
    let best = -1, bi = -1;
    for (let i = 0; i < xn.length; i++) {
        const d = Math.abs(dy * (xn[i] - xn[0]) - dx * (yn[i] - yn[0])) / den;
        if (d > best) { best = d; bi = i; }
    }
    // A nearly straight curve has no meaningful knee; do not invent one.
    if (bi <= 0 || bi >= xn.length - 1 || best < 0.02) return null;
    return { x: pts[bi].x, y: pts[bi].y, strength: best };
}

Vue.component('linechart', {
    props: {
        data: Object,
        channelData: Array,
        viewMode: String,
        metric: String,
        scaleMode: {
            type: String,
            default: 'log'
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
        data: { handler: 'render', deep: true },
        metric: 'render',
        scaleMode: 'render',
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
                container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px; text-align: center; margin-top: 20px;">Select a channel to see the dilation plot</div>';
                return;
            }

            const width = this.width;
            const height = this.height;
            const marginTop = 30;
            const marginBottom = 50;

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
                
                let displayLabel = key;
                const isCombo = key.includes('|');
                if (isCombo) {
                    const channelNames = key.split('|');
                    if (channelNames.length > 3) {
                        displayLabel = `[${channelNames.slice(0, 3).join(', ')}, ...]`;
                    } else {
                        displayLabel = `[${channelNames.join(', ')}]`;
                    }
                }

                points.forEach(p => {
                    allDilations.push(p.x);
                    allValues.push(p.y);
                });
                
                return {
                    key: key,
                    displayLabel: displayLabel,
                    points: points,
                    isCombo: isCombo
                };
            });

            if (allDilations.length === 0) {
                return;
            }

            // Room for the series labels on the right. 5.4 px/char matches the
            // 9px font they are drawn at (6.5 was sized for the old 10px), and
            // the cap stops one long combination name from eating the plot:
            // past a third of the width the curves have nowhere left to go, and
            // a clipped label is a better trade than an unreadable chart.
            const maxLabelLength = d3.max(lines, d => d.displayLabel.length);
            const marginRight = Math.min(
                Math.max(15, maxLabelLength * 5.4 + 8), width * 0.33);

            const xDomain = d3.extent(allDilations);
            if (xDomain[0] === xDomain[1]) {
                xDomain[0] -= 1;
                xDomain[1] += 1;
            }
            
            const yMax = d3.max(allValues);
            const yMin = d3.min(allValues);

            // A log y-axis is what makes the elbow legible: count and density
            // span 2-3 decades across the radius sweep, so on a linear axis the
            // whole early rise is squashed into the bottom pixel row. Log needs
            // a positive floor, hence the smallest positive value rather than
            // the `Math.max(0, ...)` this replaced.
            const positives = allValues.filter(v => v > 0 && isFinite(v));
            const minPositive = positives.length ? d3.min(positives) : 0;
            const useLog = this.scaleMode === 'log' && minPositive > 0 && yMax > minPositive;

            let yDomain;
            if (useLog) {
                yDomain = [minPositive / 1.6, yMax * 1.15];
            } else {
                const padding = (yMax - yMin) * 0.1 || (yMax * 0.1) || 0.1;
                yDomain = [Math.max(0, yMin - padding), yMax + padding];
            }

            const tickFormat = bsCompactNum;
            // Explicit tick values: 5 at most, so labels cannot pile up.
            const yTicks = useLog
                ? bsNiceLogTicks(yDomain[0], yDomain[1], 5)
                : d3.ticks(yDomain[0], yDomain[1], 5);
            // Margin must come from the ticks actually drawn, or labels clip.
            const maxTickLabelLength = d3.max(yTicks, d => tickFormat(d).length) || 0;
            const marginLeft = Math.max(50, maxTickLabelLength * 7 + 25);

            const x = d3.scaleLinear()
                .domain(xDomain)
                .range([marginLeft, width - marginRight]);

            const y = (useLog ? d3.scaleLog() : d3.scaleLinear())
                .domain(yDomain)
                .range([height - marginBottom, marginTop])
                .clamp(true);

            svg.append("g")
                .attr("transform", `translate(0,${height - marginBottom})`)
                .call(d3.axisBottom(x).ticks(5).tickFormat(d => bsCompactNum(d)))
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
                "overlap_coeff": "Overlap Coefficient",
                "density": "Density",
                "count": "Voxel Count"
            };

            svg.append("g")
                .attr("transform", `translate(${marginLeft},0)`)
                .call(d3.axisLeft(y).tickValues(yTicks).tickFormat(tickFormat))
                .call(g => g.select(".domain").remove())
                .selectAll("text")
                .attr("fill", "white")
                .style("font-size", "12px");

            svg.append("text")
                .attr("x", -marginTop - (height - marginTop - marginBottom) / 2)
                .attr("y", Math.max(12, marginLeft - (maxTickLabelLength * 7) - 20))
                .attr("transform", "rotate(-90)")
                .attr("text-anchor", "middle")
                .style("fill", "white")
                .style("font-size", "12px")
                .text(metricLabels[this.metric]);

            svg.selectAll(".domain").attr("stroke", "white");
            svg.selectAll(".tick line").attr("stroke", "#444");

            // A log axis has no position for 0, so break the line at
            // non-positive points rather than drawing them at NaN.
            const plottable = d => !useLog || (d.y > 0 && isFinite(d.y));
            const lineGen = d3.line()
                .defined(plottable)
                .x(d => x(d.x))
                .y(d => y(d.y));

            const elbows = [];
            // Series-label placement is deferred to after the loop so the
            // labels can be laid out against each other rather than each being
            // dropped at its own line end, where curves that converge (which
            // dilation curves do, they all saturate) stack them illegibly.
            const endLabels = [];
            lines.forEach(lineObj => {
                let color = "#FFFFFF";
                // Thin. Combination curves keep a little more weight so they
                // stay findable among the single-channel ones.
                let strokeWidth = lineObj.isCombo ? 1.6 : 1.1;

                const ch = this.channelData.find(c => c.name === lineObj.key);
                if (ch) {
                    color = ch.color;
                }

                svg.append("path")
                    .datum(lineObj.points)
                    .attr("fill", "none")
                    .attr("stroke", color)
                    .attr("stroke-width", strokeWidth)
                    .attr("stroke-opacity", 0.85)
                    .attr("d", lineGen);

                const drawable = lineObj.points.filter(plottable);
                if (drawable.length > 0) {
                    const lastPoint = drawable[drawable.length - 1];
                    endLabels.push({
                        x: x(lastPoint.x),
                        y: y(lastPoint.y),
                        color: color,
                        bold: !!lineObj.isCombo,
                        text: lineObj.displayLabel,
                    });
                }

                // Small dots. These mark where the samples are; at r=4 they
                // were reading as the line itself.
                drawable.forEach(p => {
                    svg.append("circle")
                        .attr("cx", x(p.x))
                        .attr("cy", y(p.y))
                        .attr("r", 1.8)
                        .attr("fill", color)
                        .attr("fill-opacity", 0.9);
                });

                // Elbow collected here, drawn after the loop so the readouts
                // can be laid out against each other.
                const elbow = bsFindElbow(lineObj.points, useLog);
                if (elbow) {
                    elbows.push({ x: elbow.x, y: elbow.y, color: color });
                }
            });

            // Series labels. Each one wants to sit at its own line's end, but
            // dilation curves converge as they saturate, so several ends land
            // within a few pixels and the labels print on top of each other.
            // Push them apart vertically instead: sort by preferred height,
            // walk down enforcing a minimum gap, then walk back up to pull the
            // block inside the plot. Each keeps a leader dot at its true end so
            // a displaced label is still traceable to its curve.
            const SERIES_H = 11;
            const plotTop = marginTop;
            const plotBottom = height - marginBottom;
            endLabels.sort((a, b) => a.y - b.y);
            let cursor = plotTop + 8;
            endLabels.forEach(l => {
                l.ly = Math.max(l.y, cursor);
                cursor = l.ly + SERIES_H;
            });
            // The block can now overflow the bottom; slide the tail back up.
            let overflow = cursor - SERIES_H - plotBottom;
            if (overflow > 0) {
                for (let i = endLabels.length - 1; i >= 0; i--) {
                    endLabels[i].ly -= overflow;
                    if (i > 0) {
                        const gap = endLabels[i].ly - endLabels[i - 1].ly;
                        if (gap >= SERIES_H) break;
                        overflow = SERIES_H - gap;
                    }
                }
            }
            endLabels.forEach(l => {
                l.ly = Math.min(Math.max(l.ly, plotTop + 8), plotBottom);
                // Leader from the curve's actual end to a displaced label, so
                // the connection survives the push.
                if (Math.abs(l.ly - l.y) > 1.5) {
                    svg.append("line")
                        .attr("x1", l.x + 2)
                        .attr("y1", l.y)
                        .attr("x2", l.x + 5)
                        .attr("y2", l.ly)
                        .attr("stroke", l.color)
                        .attr("stroke-width", 0.6)
                        .attr("stroke-opacity", 0.6);
                }
                svg.append("text")
                    .attr("x", l.x + 6)
                    .attr("y", l.ly)
                    .attr("alignment-baseline", "middle")
                    .attr("fill", l.color)
                    .style("font-size", "9px")
                    .style("font-weight", l.bold ? "bold" : "normal")
                    .text(l.text);
            });

            // Elbow markers. Curves in the same panel tend to bend at very
            // similar radii (a few tenths of a micron apart), so the readouts
            // would sit on top of each other at a single height. Stack them
            // into rows instead: each label takes the topmost row where its
            // horizontal extent is still clear.
            const LABEL_H = 11;
            const CHAR_W = 5.2;
            const rows = [];          // rows[i] = right edge occupied so far
            const seenLabels = new Set();
            const maxRows = Math.max(1, Math.floor((height - marginBottom - marginTop) / LABEL_H) - 1);
            elbows.sort((a, b) => a.x - b.x);
            elbows.forEach(e => {
                const ex = x(e.x);
                svg.append("line")
                    .attr("x1", ex)
                    .attr("x2", ex)
                    .attr("y1", marginTop)
                    .attr("y2", height - marginBottom)
                    .attr("stroke", e.color)
                    .attr("stroke-width", 1)
                    .attr("stroke-dasharray", "4,3")
                    .attr("opacity", 0.7);
                svg.append("circle")
                    .attr("cx", ex)
                    .attr("cy", y(e.y))
                    .attr("r", 5)
                    .attr("fill", "none")
                    .attr("stroke", e.color)
                    .attr("stroke-width", 2);

                // One readout per distinct radius, not per curve. Subcombinations
                // of the same channels bend at nearly the same place, so
                // labelling each one stacks a column of identical numbers; the
                // colour-coded dotted lines already say which curve is which.
                const text = bsCompactNum(e.x);
                if (seenLabels.has(text)) return;
                seenLabels.add(text);

                const w = text.length * CHAR_W + 6;
                // Flip the label to the left of its line when it would run off
                // the right edge, so it never collides with the plot border.
                const flip = ex + w > width - marginRight;
                const left = flip ? ex - w - 1 : ex + 3;
                let row = 0;
                while (row < maxRows && rows[row] !== undefined && left < rows[row]) row++;
                if (row >= maxRows) return;   // out of space: line only, no readout
                rows[row] = left + w;
                svg.append("text")
                    .attr("x", left)
                    .attr("y", marginTop + 9 + row * LABEL_H)
                    .attr("fill", "#FFFFFF")
                    .style("font-size", "9px")
                    .text(text);
            });
        }
    }
});
