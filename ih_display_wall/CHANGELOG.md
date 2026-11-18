# Changelog

## 0.12.0

- Add an optional authenticated PNG endpoint for the app-owned display preview.
- Include optional read-only active media and binary overlay sources in the preview.
- Import validated private source mappings through admin ingress without a restart.

## 0.11.0

- Add an optional authenticated compatibility image endpoint and more weather details.

## 0.10.0

- Add current forecast conditions and daily high/low predictions to the weather preview.

## 0.9.0

- Add an optional read-only hourly forecast preview with a fallback source and unavailable state.

## 0.8.0

- Add an optional read-only urgent alert banner to the wall preview.

## 0.7.0

- Highlight urgent items in the shared task preview without changing their source order.

## 0.6.0

- Read the shared task feed, including the optional dishwasher task.

## 0.5.1

- Label the task preview as selected sources so an empty partial migration is not presented as a complete task list.

## 0.5.0

- Optionally show configured shared display messages in the task preview.

## 0.4.0

- Combine an optional structured task feed with a to-do list and show when a source is unavailable.

## 0.3.0

- Add a side-by-side, profile-sized display rendering prototype using configured read-only Home Assistant data.

## 0.2.0

- Optionally read a wall profile from IH Displays without depending on it for picture preview.

## 0.1.1

- Use published prebuilt images for installation and updates.

## 0.1.0

- Add a private wall-display picture preview with the last successful frame retained during temporary source failures.
