import sa.kps.driver.LocationQueuePolicy;
public final class LocationQueuePolicyTest {
    public static void main(String[] args) {
        long now=1800000000000L;
        check(LocationQueuePolicy.retained(now,now));
        check(LocationQueuePolicy.retained(now-LocationQueuePolicy.MAX_AGE_MS,now));
        check(!LocationQueuePolicy.retained(now-LocationQueuePolicy.MAX_AGE_MS-1,now));
        check(!LocationQueuePolicy.retained(now+30001,now));
        check(!LocationQueuePolicy.retained(0,now));
        check(LocationQueuePolicy.sameScope("user/shift/session1","user/shift/session1"));
        check(!LocationQueuePolicy.sameScope("user/shift/session1","user/shift/session2"));
        check(!LocationQueuePolicy.sameScope("user/shift1/session","user/shift2/session"));
        check(!LocationQueuePolicy.sameScope("", ""));
        check(!LocationQueuePolicy.sameScope(null,null));
        check(LocationQueuePolicy.BATCH_SIZE==100);
        check(LocationQueuePolicy.MAX_POINTS*20000L==LocationQueuePolicy.MAX_AGE_MS);
        System.out.println("12 offline retention/session/batch limit checks PASS");
    }
    private static void check(boolean value){if(!value)throw new AssertionError();}
}
