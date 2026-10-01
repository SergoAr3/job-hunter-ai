"use client";

import {
  applicationStatuses,
  type ApplicationStatus,
} from "../lib/applications";
import { statusLabels } from "./vacancy";
import { ListboxSelect } from "./listbox-select";

export function StatusSelect({
  value,
  disabled,
  onChange,
  labelId,
}: {
  value: ApplicationStatus;
  disabled: boolean;
  onChange: (status: ApplicationStatus) => void;
  labelId: string;
}) {
  return (
    <ListboxSelect
      id="application-status"
      labelId={labelId}
      value={value}
      options={applicationStatuses}
      labels={statusLabels}
      disabled={disabled}
      onChange={onChange}
    />
  );
}
