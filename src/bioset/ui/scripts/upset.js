(function initUpSet() {
  const container = document.getElementById('upset-container');
  if (!container) {
    setTimeout(initUpSet, 100);
    return;
  }

  if (!window.UpSetJS) {
    setTimeout(initUpSet, 200);
    return;
  }

  if (!window.trame || !window.trame.state) {
    setTimeout(initUpSet, 200);
    return;
  }

  // Colors matching the React component (dark theme)
  const WHITE = '#FFFFFF';
  const LIGHT_GRAY = '#A0A0A0';
  const GRAY = '#4A4A4A';

  // Track current selection
  let currentSelection = null;

  // Transform data from state format to UpSetJS format
  function transformData(upsetData) {
    if (!upsetData || upsetData.length === 0) {
      return { sets: [], combinations: [] };
    }

    // Convert from {channels: [...], count: int} to UpSetJS expression format
    // Use cardinality to pass actual count values (not just count elements)
    const mappedData = upsetData.map((item) => ({
      sets: item.channels,
      cardinality: item.count,
    }));

    return UpSetJS.extractFromExpression(mappedData);
  }

  // Render the UpSet plot
  function renderUpset(upsetData, selection) {
    const { sets, combinations } = transformData(upsetData);

    // Determine primary color based on selection
    const primaryColor = selection ? LIGHT_GRAY : WHITE;

    container.innerHTML = "";
    UpSetJS.render(container, {
      sets,
      combinations,
      width: 330,
      height: 340,
      theme: 'dark',
      color: primaryColor,
      textColor: WHITE,
      selectionColor: WHITE,
      notMemberColor: GRAY,
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
      selection: selection,
      onClick: (clickedItem) => {
        if (!clickedItem) {
          // Clicked on empty space - clear selection
          currentSelection = null;
          window.trame.state.set("upset_selection", null);
          window.trame.state.set("upset_click", null);
          renderUpset(window.trame.state.get("upset_data_reduced"), null);
          return;
        }

        // Toggle selection
        if (currentSelection && currentSelection.name === clickedItem.name) {
          // Clicked same item - deselect
          currentSelection = null;
          window.trame.state.set("upset_selection", null);
          window.trame.state.set("upset_click", null);
        } else {
          // Select new item
          currentSelection = clickedItem;

          // Get set names from the clicked item
          const setNames = clickedItem.sets
            ? Array.from(clickedItem.sets).map(set => set.name)
            : [clickedItem.name];

          window.trame.state.set("upset_selection", {
            name: clickedItem.name,
            sets: setNames,
            cardinality: clickedItem.cardinality,
          });

          window.trame.state.set("upset_click", {
            name: clickedItem.name,
            sets: setNames,
            size: clickedItem.cardinality,
            ts: Date.now(),
          });
        }

        // Re-render with new selection
        renderUpset(window.trame.state.get("upset_data_reduced"), currentSelection);
      },
    });
  }

  // Watch for data changes using trame's state watcher
  window.trame.state.watch(["upset_data_reduced"], (newData) => {
    console.log("[upset.js] Data updated:", newData ? newData.length : 0, "combinations");
    currentSelection = null; // Reset selection when data changes
    renderUpset(newData, null);
  });

  // Initial render
  const initialData = window.trame.state.get("upset_data_reduced");
  renderUpset(initialData, null);

  console.log("[upset.js] UpSet plot initialized");
})();