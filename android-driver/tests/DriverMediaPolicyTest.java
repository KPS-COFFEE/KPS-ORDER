import sa.kps.driver.DriverMediaPolicy;

public class DriverMediaPolicyTest {
    private static void check(boolean condition){if(!condition)throw new AssertionError();}
    public static void main(String[] args){
        String work="https://order.kps.sa/ops",video="android.webkit.resource.VIDEO_CAPTURE";
        check(DriverMediaPolicy.work(work));check(DriverMediaPolicy.work("https://order.kps.sa:443/ops"));
        for(String bad:new String[]{"http://order.kps.sa/ops","https://order.kps.sa.evil/ops","https://order.kps.sa:444/ops","https://evil@order.kps.sa/ops","content://order.kps.sa/ops","https://order.kps.sa%2eevil/ops","",null}){
            check(!DriverMediaPolicy.work(bad));check(!DriverMediaPolicy.videoRequest(bad,work,new String[]{video}));
            check(!DriverMediaPolicy.videoRequest(work,bad,new String[]{video}));
        }
        check(DriverMediaPolicy.videoRequest(work,work,new String[]{video}));
        check(!DriverMediaPolicy.videoRequest(work,work,new String[]{video,"android.webkit.resource.AUDIO_CAPTURE"}));
        check(!DriverMediaPolicy.videoRequest(work,work,new String[]{"android.webkit.resource.AUDIO_CAPTURE"}));
        check(!DriverMediaPolicy.videoRequest(work,work,new String[]{}));
        check(!DriverMediaPolicy.videoRequest(work,work,null));
        check(DriverMediaPolicy.cameraResult(true,true,work));
        check(!DriverMediaPolicy.cameraResult(true,false,work)); // runtime denial
        check(!DriverMediaPolicy.cameraResult(false,true,work)); // canceled/replaced request
        check(!DriverMediaPolicy.cameraResult(true,true,"https://evil/")); // navigated during permission prompt
        check(DriverMediaPolicy.external("https://wa.me/966500000001?text=hello",false)); // async handoff retains exact authority
        for(String bad:new String[]{"https://wa.me.evil/966500000001","https://evil@wa.me/966500000001","https://wa.me:444/966500000001","javascript:alert(1)","intent://wa.me/","http://wa.me/966500000001"})check(!DriverMediaPolicy.external(bad,false));
        check(DriverMediaPolicy.external("https://www.google.com/maps?q=1,2",true));
        check(!DriverMediaPolicy.external("https://www.google.com/maps?q=1,2",false));
        System.out.println("Driver media authority, runtime denial/cancel and async WhatsApp policy PASS");
    }
}
