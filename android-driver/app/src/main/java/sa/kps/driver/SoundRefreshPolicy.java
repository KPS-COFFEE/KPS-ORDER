package sa.kps.driver;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;

/** Optional sound metadata must never become a minute-by-minute missing-route poll. */
public final class SoundRefreshPolicy {
    public static final long NORMAL_RETRY_MS=60000L;
    public static final long UNSUPPORTED_RETRY_MS=6L*60*60*1000;
    private final Map<String,Long> nextCheck=new HashMap<>();

    public void retain(Set<String> brands){nextCheck.keySet().retainAll(brands);}
    public boolean begin(String brand,long now){
        if(now<nextCheck.getOrDefault(brand,0L))return false;
        nextCheck.put(brand,now+NORMAL_RETRY_MS);
        return true;
    }
    public void unavailable(String brand,long now,int status){
        if(status==404||status==501)nextCheck.put(brand,now+UNSUPPORTED_RETRY_MS);
    }
}
