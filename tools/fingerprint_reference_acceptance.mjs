export function referenceAcceptanceProbe() {
  const offset = [];
  for (const [width, height] of [[120, 80], [50, 50]]) {
    const element = document.createElement("div");
    element.style.cssText = `position:absolute;box-sizing:content-box;width:${width}px;height:${height}px;padding:0;border:0;transform:none`;
    document.body.append(element);
    const measure = () => {
      const bounds = element.getBoundingClientRect();
      return [element.offsetWidth, element.offsetHeight, element.clientWidth, element.clientHeight,
        bounds.width, bounds.height];
    };
    try {
      offset.push({width, height, first:measure(), repeat:measure()});
    } finally {
      element.remove();
    }
  }
  return {version:1, offset};
}

export function validateReferenceAcceptance(records) {
  const samples = records.all_native?.extra?.reference?.offset;
  const checks = [[120,80],[50,50]].map(([width,height]) => {
    const row = samples?.find(sample => sample.width === width && sample.height === height);
    const expected = JSON.stringify([width,height,width,height,width,height]);
    return {scenario:"all_native", name:`offset_${width}_native_control`,
      passed:JSON.stringify(row?.first) === expected && JSON.stringify(row?.repeat) === expected};
  });
  return {checks, not_tested:[]};
}
