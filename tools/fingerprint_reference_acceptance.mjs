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

export async function layoutAndQuotaProbe() {
  const values = ({x,y,width,height}) => [x,y,width,height];
  const detached = document.createElement("span");
  const span = document.createElement("span");
  const range = document.createRange();
  range.selectNodeContents(detached);
  const empty = [values(detached.getBoundingClientRect()), values(range.getBoundingClientRect())];
  span.style.cssText = "white-space:pre;font:16px monospace";
  span.textContent = "alpha\nbeta\ngamma";
  document.body.append(span);
  try {
    range.selectNodeContents(span);
    const read = node => Array.from(node.getClientRects(),values);
    const first = [read(span),read(range)];
    const repeat = [read(span),read(range)];
    const {quota} = await navigator.storage.estimate();
    return {version:1, empty, first, repeat, quota};
  } finally {
    span.remove();
  }
}

export function validLayoutPolicyEvidence(record) {
  const sample = record?.extra?.slim_policy;
  const rect = value => Array.isArray(value) && value.length===4 && value.every(Number.isFinite);
  return sample?.version===1 &&
    Array.isArray(sample.empty) && sample.empty.length===2 &&
    sample.empty.every(value=>rect(value)&&value.every(n=>n===0)) &&
    Array.isArray(sample.first) && sample.first.length===2 &&
    sample.first.every(rows=>Array.isArray(rows)&&rows.length>=2&&rows.every(rect)) &&
    JSON.stringify(sample.first)===JSON.stringify(sample.repeat) &&
    Number.isSafeInteger(sample.quota) && sample.quota>0 &&
    (record.id!=="memory_custom" || sample.quota===64*1024*1024);
}
