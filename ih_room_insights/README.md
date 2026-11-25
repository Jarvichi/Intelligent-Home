# Intelligent Home - Room Insights
Read-only. Learns from Home Assistant history how long each room takes to warm up, flags valves whose reading is stuck, and flags long high humidity. Ideas borrowed from RoomMind (not installed). It never changes a setpoint, never calls a service and sends no notification.

Since 0.2.0 it has a read-only page in the side panel (ingress, port 8813) showing the same results: warm-up minutes per room, suspect valves, mould risk.
