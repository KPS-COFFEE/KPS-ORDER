package sa.kps.driver;

import java.util.*;

/** Deterministic queue state; persistence and Android lifecycle live in the adapter. */
public final class LocationQueueState {
    public static final class Point {
        public final String id;
        public final long capturedAt;
        public final double lat,lng,accuracy;
        public Point(String id,long capturedAt,double lat,double lng,double accuracy) {
            this.id=id;this.capturedAt=capturedAt;this.lat=lat;this.lng=lng;this.accuracy=accuracy;
        }
    }
    private final List<Point> points=new ArrayList<>();
    private int dropped;
    public LocationQueueState(int previousDropped){dropped=Math.max(0,previousDropped);}
    public void append(Point point,long now) {
        prune(now);
        if(!LocationQueuePolicy.retained(point.capturedAt,now)){dropped++;return;}
        // Recovered duplicate IDs cannot occupy multiple queue slots.
        for(Point existing:points)if(existing.id.equals(point.id))return;
        if(points.size()==LocationQueuePolicy.MAX_POINTS){points.remove(0);dropped++;}
        points.add(point);
    }
    public void prune(long now) {
        Iterator<Point> it=points.iterator();
        while(it.hasNext())if(!LocationQueuePolicy.retained(it.next().capturedAt,now)){it.remove();dropped++;}
    }
    public List<Point> snapshot(){return new ArrayList<>(points);}
    public List<Point> batch(long now){
        prune(now);
        int count=Math.min(points.size(),LocationQueuePolicy.BATCH_SIZE);
        List<Point> batch=new ArrayList<>(points.subList(0,count));
        // Reconnect must not wait behind hours of history before publishing a live fix.
        // Older records still drain; the server orders history by capture time.
        if(points.size()>count)batch.set(count-1,points.get(points.size()-1));
        return batch;
    }
    public void acknowledge(Set<String> ids){points.removeIf(point->ids.contains(point.id));}
    public int size(){return points.size();}
    public int dropped(){return dropped;}
}
