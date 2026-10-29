package sa.kps.driver;

import java.net.URI;

/** Authority decisions shared by actual WebView callbacks and JVM regression tests. */
public final class DriverMediaPolicy {
    private DriverMediaPolicy() {}
    private static boolean host(String url,String expected){
        try{
            URI uri=new URI(url);
            return "https".equals(uri.getScheme()) && expected.equals(uri.getHost())
                && uri.getRawUserInfo()==null && (uri.getPort()==-1 || uri.getPort()==443);
        }catch(Exception invalid){return false;}
    }
    public static boolean work(String url){return host(url,"order.kps.sa");}
    public static boolean whatsapp(String url){return host(url,"wa.me");}
    public static boolean videoRequest(String origin,String page,String[] resources){
        return work(origin) && work(page) && resources!=null && resources.length==1
            && "android.webkit.resource.VIDEO_CAPTURE".equals(resources[0]);
    }
    public static boolean cameraResult(boolean pending,boolean granted,String currentPage){
        return pending && granted && work(currentPage);
    }
    public static boolean external(String url,boolean gesture){
        if(whatsapp(url))return true;
        try{return gesture && "https".equals(new URI(url).getScheme());}catch(Exception invalid){return false;}
    }
}
