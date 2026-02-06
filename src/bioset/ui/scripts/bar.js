(function initBarChart() {
    const container = document.getElementById('bar-container');
    if (!container) {
        setTimeout(initBarChart, 100);
        return;
    }

    if (!window.d3) {
        setTimeout(initBarChart, 200);
        return;
    }

    if (!window.trame || !window.trame.state) {
        setTimeout(initBarChart, 200);
        return;
    }

    // Colors
    const WHITE = '#FFFFFF';
    const LIGHT_GRAY = '#A0A0A0';

    // Track selection
    let selectedBars = new Set();

    // Render the bar chart
    function renderBarChart(data) {
        // Clear previous content
        d3.select(container).selectAll("*").remove();

        if (!data || data.length === 0) {
            container.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 14px;">No channel data available</div>';
            return;
        }

        // Determine primary color based on selection
        const primaryColor = selectedBars.size > 0 ? LIGHT_GRAY : WHITE;
        const selectedColor = WHITE;

        // Chart dimensions
        const width = 330;
        const height = 280;
        const marginTop = 30;
        const marginRight = 10;
        const marginBottom = 120;
        const marginLeft = 80;

        // Parse data: [[channel_name, count], ...]
        const chartData = data.map(d => ({ name: d[0], count: d[1] }));

        // Scales
        const x = d3.scaleBand()
            .domain(chartData.map(d => d.name))
            .range([marginLeft, width - marginRight])
            .padding(0.1);

        const y = d3.scaleLinear()
            .domain([0, d3.max(chartData, d => d.count)])
            .range([height - marginBottom, marginTop]);

        // Create SVG
        const svg = d3.select(container)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("viewBox", [0, 0, width, height])
            .attr("style", "max-width: 100%; height: auto;");

        // Add bars
        svg.append("g")
            .selectAll("rect")
            .data(chartData)
            .join("rect")
            .attr("x", d => x(d.name))
            .attr("y", d => y(d.count))
            .attr("height", d => y(0) - y(d.count))
            .attr("width", x.bandwidth())
            .attr("fill", d => selectedBars.has(d.name) ? selectedColor : primaryColor)
            .attr("cursor", "pointer")
            .on("click", (event, d) => {
                // Toggle selection
                if (selectedBars.has(d.name)) {
                    selectedBars.delete(d.name);
                } else {
                    selectedBars.add(d.name);
                }

                // Update trame state
                window.trame.state.set("bar_selection", Array.from(selectedBars));
                window.trame.state.set("bar_click", {
                    name: d.name,
                    count: d.count,
                    selected: selectedBars.has(d.name),
                    ts: Date.now(),
                });

                // Re-render
                renderBarChart(window.trame.state.get("bar_data_reduced"));
            })
            .on("mouseover", function () {
                d3.select(this)
                    .attr("opacity", 0.8)
                    .attr("stroke", WHITE)
                    .attr("stroke-width", 2);
            })
            .on("mouseout", function () {
                d3.select(this)
                    .attr("opacity", 1)
                    .attr("stroke", "none");
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

        // Style x-axis line
        svg.select(".domain").attr("stroke", "white");
        svg.selectAll(".tick line").attr("stroke", "white");

        // Y-axis
        svg.append("g")
            .attr("transform", `translate(${marginLeft},0)`)
            .call(d3.axisLeft(y).tickFormat(d3.format(".2e")))
            .call(g => g.select(".domain").remove())
            .call(g => g
                .append("text")
                .attr("x", -marginLeft)
                .attr("y", 10)
                .attr("fill", "white")
                .attr("text-anchor", "start")
                .text("↑ Frequency"))
            .selectAll("text")
            .attr("fill", "white")
            .style("font-size", "12px");
    }

    // Watch for data changes using trame's state watcher
    window.trame.state.watch(["bar_data_reduced"], (newData) => {
        console.log("[bar.js] Data updated:", newData ? newData.length : 0, "channels");
        selectedBars.clear(); // Reset selection when data changes
        renderBarChart(newData);
    });

    // Initial render
    const initialData = window.trame.state.get("bar_data_reduced");
    renderBarChart(initialData);

    console.log("[bar.js] Bar chart initialized");
})();
