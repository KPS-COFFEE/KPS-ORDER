package sa.kps.driver;

/** Pure decision policy, shared by Activity/service; no device or server state. */
public final class TrackingPolicy {
    private TrackingPolicy() {}
    public static boolean allowed(String role, boolean active, String user, String shiftUser,
                                  String kind, String status, boolean branchAllowed) {
        return "driver".equals(role) && active && user != null && !user.isEmpty()
            && user.equals(shiftUser) && "driver".equals(kind) && "open".equals(status) && branchAllowed;
    }
    public static boolean fresh(double lat, double lng, float accuracy, long ageMs, boolean mock) {
        return !mock && Double.isFinite(lat) && Double.isFinite(lng) && Math.abs(lat) <= 90
            && Math.abs(lng) <= 180 && Float.isFinite(accuracy) && accuracy >= 0 && accuracy <= 1000
            && ageMs >= 0 && ageMs <= 30000;
    }
}
