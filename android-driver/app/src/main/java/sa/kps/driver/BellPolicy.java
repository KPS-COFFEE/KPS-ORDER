package sa.kps.driver;

import java.util.*;

/** Monotonic, memory-only alert state. Reading/opening never acknowledges work. */
public final class BellPolicy {
    private static final class Item { final long since; boolean first,second; Item(long n){since=n;} }
    private final Map<String,Item> items=new HashMap<>();
    private long verified=-1,lastRing=-15000;
    private long first=0,second=30000,continuous=60000;
    public static boolean pending(String status,String driver,String user,String transfer) {
        if(user==null||user.isEmpty()||driver==null||driver.isEmpty())return false;
        if(!Arrays.asList("assigned","accepted","picked_up","problem").contains(status))return false;
        return user.equals(driver)?"assigned".equals(status):transfer!=null&&!transfer.isEmpty()&&!"null".equals(transfer);
    }
    public void configure(int firstSeconds,int secondSeconds,int continuousSeconds){
        if(firstSeconds<0||secondSeconds<=firstSeconds||continuousSeconds<=secondSeconds||continuousSeconds>1800)return;
        first=firstSeconds*1000L;second=secondSeconds*1000L;continuous=continuousSeconds*1000L;
    }
    public void update(Set<String> pending,long now){
        items.keySet().retainAll(pending);
        for(String id:pending)if(id!=null&&!id.isEmpty())items.computeIfAbsent(id,k->new Item(now));
        verified=now;
    }
    public void invalidate(){verified=-1;}
    public void clear(){items.clear();verified=-1;lastRing=-15000;}
    public int count(){return items.size();}
    public boolean ring(long now){
        if(verified<0||now<verified||now-verified>30000||now-lastRing<15000)return false;
        boolean due=false;
        for(Item item:items.values()){
            long age=now-item.since;
            if(!item.first&&age>=first){item.first=true;due=true;}
            if(!item.second&&age>=second){item.second=true;due=true;}
            if(age>=continuous)due=true;
        }
        if(due)lastRing=now;
        return due;
    }
}
