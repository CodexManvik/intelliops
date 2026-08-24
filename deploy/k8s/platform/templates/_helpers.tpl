{{/*
Expand the name of the chart.
*/}}
{{- define "intelliops.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
*/}}
{{- define "intelliops.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources in the chart.
*/}}
{{- define "intelliops.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Selector labels used in matchLabels and pod template labels.
Accepts a dict with "root" (the top-level .) and "name" (the service name).
Usage: {{ include "intelliops.selectorLabels" (dict "root" . "name" .name) }}
*/}}
{{- define "intelliops.selectorLabels" -}}
app.kubernetes.io/name: {{ .name | quote }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}
