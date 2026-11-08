(function () {
  function palette() {
    const style = getComputedStyle(document.documentElement);
    const token = name => style.getPropertyValue(name).trim();
    return {
      accent: token('--color-accent'), info: token('--color-info'),
      text: token('--color-text'), muted: token('--color-muted'),
      mutedStrong: token('--color-muted-strong'),
      grid: token('--chart-grid'), fill: token('--chart-fill'),
      warning: token('--color-warning'), critical: token('--color-critical'),
      markerLine: token('--chart-marker-line'), markerText: token('--chart-marker-text'),
    };
  }

  // Series differ in lightness and dash, not only in hue: accent, light gray, dashed gray.
  function datasets(series) {
    const theme = palette();
    const colours = [theme.accent, theme.mutedStrong, theme.muted];
    const dashes = [[], [], [4, 4]];
    return series.map((item, index) => ({
      borderDash: dashes[index % dashes.length],
      ...item, borderColor: colours[index % colours.length], backgroundColor: theme.fill,
    }));
  }

  function legend(series) {
    return { display: series.length > 1, labels: { color: palette().text } };
  }

  function thresholds(warning, critical) {
    const theme = palette();
    return { levels: [
      { label: 'Warning', value: warning, color: theme.warning },
      { label: 'Critical', value: critical, color: theme.critical },
    ] };
  }

  const thresholdPlugin = {
    id: 'temperatureThresholds',
    afterDraw(chart, _args, options) {
      if (!chart.scales.y || !options.levels) return;
      const ctx = chart.ctx;
      const area = chart.chartArea;
      const padding = 2;
      const gap = 4;
      ctx.save();
      ctx.setLineDash([6, 6]);
      ctx.textAlign = 'right';
      ctx.textBaseline = 'alphabetic';
      ctx.font = '12px system-ui, sans-serif';
      // Place measured text boxes from top to bottom, then back up from the plot bottom.
      // A close pair at either edge must not be clamped to overlapping baselines.
      const levels = options.levels
        .map((level) => {
          const metrics = ctx.measureText(level.label);
          return {
            ...level, y: chart.scales.y.getPixelForValue(level.value),
            ascent: Math.ceil(metrics.actualBoundingBoxAscent),
            descent: Math.ceil(metrics.actualBoundingBoxDescent),
          };
        })
        .sort((a, b) => a.y - b.y);
      let previousBottom = area.top + padding - gap;
      for (const level of levels) {
        level.baseline = Math.max(level.y - 4, previousBottom + gap + level.ascent);
        previousBottom = level.baseline + level.descent;
      }
      let nextTop = area.bottom - padding + gap;
      for (let index = levels.length - 1; index >= 0; index -= 1) {
        const level = levels[index];
        level.baseline = Math.min(level.baseline, nextTop - gap - level.descent);
        nextTop = level.baseline - level.ascent;
      }
      for (const level of levels) {
        ctx.strokeStyle = level.color;
        ctx.fillStyle = level.color;
        ctx.beginPath();
        ctx.moveTo(area.left, level.y);
        ctx.lineTo(area.right, level.y);
        ctx.stroke();
        ctx.fillText(level.label, area.right - 6, level.baseline);
      }
      ctx.restore();
    },
  };

  window.chartTheme = { palette, datasets, legend, thresholds, thresholdPlugin };
}());
