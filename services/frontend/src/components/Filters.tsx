export interface FilterState {
  layer: "all" | "politicians" | "organizations";
  level?: "federal" | "provincial" | "municipal";
  province?: string;
}

interface Props {
  value: FilterState;
  onChange: (next: FilterState) => void;
}

export function Filters({ value, onChange }: Props) {
  return (
    <div className="filters">
      <label className="filters__field">
        <span>Layer</span>
        <select
          value={value.layer}
          onChange={e => onChange({ ...value, layer: e.target.value as FilterState["layer"] })}
        >
          <option value="all">All</option>
          <option value="politicians">Politicians</option>
          <option value="organizations">Organizations</option>
        </select>
      </label>

      <label className="filters__field">
        <span>Level</span>
        <select
          value={value.level ?? ""}
          onChange={e => onChange({ ...value, level: (e.target.value || undefined) as FilterState["level"] })}
        >
          <option value="">Any</option>
          <option value="federal">Federal</option>
          <option value="provincial">Provincial</option>
          <option value="municipal">Municipal</option>
        </select>
      </label>

      <label className="filters__field">
        <span>Province</span>
        <select
          value={value.province ?? ""}
          onChange={e => onChange({ ...value, province: e.target.value || undefined })}
        >
          <option value="">All</option>
          {["AB","BC","MB","NB","NL","NS","ON","PE","QC","SK","NT","NU","YT"].map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </label>
    </div>
  );
}
