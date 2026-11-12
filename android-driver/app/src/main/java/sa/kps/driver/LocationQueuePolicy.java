package sa.kps.driver;

/** Limits are explicit: gaps after expiry/overflow are not counted as measured travel. */
public final class LocationQueuePolicy {
    public static final int MAX_POINTS=4320; // 24 hours at the 20-second capture cadence.
    public static final int BATCH_SIZE=100;
    public static final long MAX_AGE_MS=24L*60*60*1000;
    private LocationQueuePolicy() {}
    public static boolean retained(long captured,long now) {
        return captured>0 && captured<=now+30000 && now-captured<=MAX_AGE_MS;
    }
    public static boolean sameScope(String expected,String actual) {
        return expected!=null && !expected.isEmpty() && expected.equals(actual);
    }
}
