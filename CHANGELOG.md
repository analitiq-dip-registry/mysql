# Changelog

## [0.1.1] - 2026-06-16

### Fixed
- fix: override `current_timestamp_default()` to return `CURRENT_TIMESTAMP(6)` so `DATETIME(6)` columns have a valid default (MySQL error 1067) (#14)

## [0.1.0] - 2026-06-04

### Added
- feat: package connector with CDK dialect class and driver deps (#12)

## [0.0.4] - 2026-05-15

### Fixed
- bug: match Analitiq webhook API Gateway schema exactly (#9)

## [0.0.3] - 2026-04-27

### Fixed
- feat: consolidate manifest into connector.json and add type map (#5)

## [0.0.2] - 2026-04-21

### Fixed
- docs: point engine references to analitiq-ai/analitiq-engine (#4)

## [0.0.1] - 2026-03-29

### Added
- Initial connector definition with database credentials authentication
