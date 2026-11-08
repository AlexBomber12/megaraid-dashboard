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
      const lineHeight = 14;
      ctx.save();
      ctx.setLineDash([6, 6]);
      ctx.textAlign = 'right';
      // Labels sit at the right edge, inside the plot, highest level first; a label that would
      // touch the previous one, or leave the plot, moves below its line instead.
      const levels = options.levels
        .map((level) => ({ ...level, y: chart.scales.y.getPixelForValue(level.value) }))
        .sort((a, b) => a.y - b.y);
      let previousBottom = -Infinity;
      for (const level of levels) {
        ctx.strokeStyle = level.color;
        ctx.fillStyle = level.color;
        ctx.beginPath();
        ctx.moveTo(area.left, level.y);
        ctx.lineTo(area.right, level.y);
        ctx.stroke();
        let baseline = level.y - 4;
        if (baseline - lineHeight < Math.max(area.top, previousBottom)) baseline = level.y + lineHeight;
        baseline = Math.min(baseline, area.bottom - 2);
        ctx.fillText(level.label, area.right - 6, baseline);
        previousBottom = baseline;
      }
      ctx.restore();
    },
  };

  window.chartTheme = { palette, datasets, legend, thresholds, thresholdPlugin };
}());
