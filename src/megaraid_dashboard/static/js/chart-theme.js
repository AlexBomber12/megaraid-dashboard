(function () {
  function palette() {
    const style = getComputedStyle(document.documentElement);
    const token = name => style.getPropertyValue(name).trim();
    return {
      accent: token('--color-accent'), info: token('--color-info'),
      text: token('--color-text'), muted: token('--color-muted'),
      grid: token('--chart-grid'), fill: token('--chart-fill'),
      warning: token('--color-warning'), critical: token('--color-critical'),
      markerLine: token('--chart-marker-line'), markerText: token('--chart-marker-text'),
    };
  }

  function datasets(series) {
    const theme = palette();
    const colours = [theme.accent, theme.info, theme.muted];
    return series.map((item, index) => ({
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
      ctx.save();
      ctx.setLineDash([6, 6]);
      for (const level of options.levels) {
        const y = chart.scales.y.getPixelForValue(level.value);
        ctx.strokeStyle = level.color;
        ctx.fillStyle = level.color;
        ctx.beginPath();
        ctx.moveTo(area.left, y);
        ctx.lineTo(area.right, y);
        ctx.stroke();
        ctx.fillText(level.label, area.left + 8, y - 6);
      }
      ctx.restore();
    },
  };

  window.chartTheme = { palette, datasets, legend, thresholds, thresholdPlugin };
}());
