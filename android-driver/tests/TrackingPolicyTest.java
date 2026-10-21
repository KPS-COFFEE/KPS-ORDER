import sa.kps.driver.TrackingPolicy;

public final class TrackingPolicyTest {
    public static void main(String[] args) {
        check(TrackingPolicy.allowed("driver",true,"one","one","driver","open",true));
        check(!TrackingPolicy.allowed("owner",true,"one","one","driver","open",true));
        check(!TrackingPolicy.allowed("driver",false,"one","one","driver","open",true));
        check(!TrackingPolicy.allowed("driver",true,"one","other","driver","open",true));
        check(!TrackingPolicy.allowed("driver",true,"one","one","driver","closed",true));
        check(!TrackingPolicy.allowed("driver",true,"one","one","driver","closing",true));
        check(!TrackingPolicy.allowed("driver",true,"one","one","manager","open",true));
        check(!TrackingPolicy.allowed("driver",true,"one","one","driver","open",false));
        check(TrackingPolicy.fresh(28.4,45.9,10,1000,false));
        check(!TrackingPolicy.fresh(28.4,45.9,10,31000,false));
        check(!TrackingPolicy.fresh(28.4,45.9,10,-1,false));
        check(!TrackingPolicy.fresh(28.4,45.9,10,1,true));
        check(!TrackingPolicy.fresh(Double.NaN,45.9,10,1,false));
        check(!TrackingPolicy.fresh(91,45.9,10,1,false));
        check(!TrackingPolicy.fresh(28.4,181,10,1,false));
        check(!TrackingPolicy.fresh(28.4,45.9,1001,1,false));
        check(!TrackingPolicy.fresh(28.4,45.9,Float.NaN,1,false));
        System.out.println("17 tracking authority/freshness cases PASS");
    }
    private static void check(boolean result){if(!result)throw new AssertionError();}
}
