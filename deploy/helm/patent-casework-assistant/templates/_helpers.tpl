{{- define "patent-casework-assistant.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patent-casework-assistant.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "patent-casework-assistant.labels" -}}
helm.sh/chart: {{ include "patent-casework-assistant.name" . }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "patent-casework-assistant.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "patent-casework-assistant.selectorLabels" -}}
app.kubernetes.io/name: {{ include "patent-casework-assistant.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "patent-casework-assistant.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "patent-casework-assistant.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "patent-casework-assistant.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- include "patent-casework-assistant.fullname" . -}}
{{- end -}}
{{- end -}}

{{- define "patent-casework-assistant.pvc.name" -}}
{{- printf "%s-%s" (include "patent-casework-assistant.fullname" .) . -}}
{{- end -}}
